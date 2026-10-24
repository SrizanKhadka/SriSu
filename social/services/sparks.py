"""Sparks catalogue and serialized, original-audience mutual reveal.

No answer plaintext, room keys, or access tokens enter this service. Django is
trusted for release timing, not answer confidentiality (see docs/sparks.md).
"""
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import APIException
from chat.models import ChatRoom, MatrixRoomMapping, MatrixUserMapping
from social.models import SparkSession, SparkAnswer
from social.services.moment_publication import current_couple

DECKS = [
    ("deep", "Deep", "A little closer", [
        "What do you admire about me that you’ve never told me?",
        "When do you feel most understood by me?",
        "What helps you feel loved on a difficult day?",
        "What is something you would like us to understand better about each other?",
        "What small thing do I do that means more than I realise?",
        "What does feeling safe together mean to you?",
    ]),
    ("memories", "Memories", "Back to us", [
        "Which ordinary day together would you happily live again?",
        "What do you remember most clearly about our first conversation?",
        "When did you first feel that we made a good team?",
        "What is a little moment with me that you still smile about?",
        "Which place holds a special memory of us?",
        "What song takes you back to a moment we shared?",
    ]),
    ("playful", "Playful", "Make room for laughter", [
        "If we had a whole day with no responsibilities, what would we do?",
        "What would our completely impractical dream date look like?",
        "If we opened a tiny shop together, what would we sell?",
        "What is our funniest shared habit?",
        "Which fictional world would you visit with me for a day?",
        "What would you put on a surprise picnic menu for us?",
    ]),
    ("future", "Future", "Things to look forward to", [
        "What is one small tradition you would love us to start?",
        "What would a lovely ordinary weekend together look like a year from now?",
        "What is something new you would like us to learn together?",
        "Which place would you love to explore with me?",
        "What is one way we can make more time for each other?",
        "What shared dream would you like to take one small step towards?",
    ]),
]
CATALOGUE = [{"id": key, "name": name, "subtitle": subtitle,
    "questions": [{"id": f"{key}-{i + 1}-v1", "deck": key, "text": text} for i, text in enumerate(questions)]}
    for key, name, subtitle, questions in DECKS]
QUESTIONS = {q["id"]: q for deck in CATALOGUE for q in deck["questions"]}


class SparkConflict(APIException):
    status_code = 409
    default_code = "spark_changed"
    default_detail = "This Spark changed. Refresh to continue."


def room_for(couple, members):
    room = ChatRoom.objects.filter(couple=couple).first()
    mapping = MatrixRoomMapping.objects.filter(chat_room=room, state="active", replacement_pending=False).first() if room else None
    identities = list(MatrixUserMapping.objects.filter(user_id__in=[m.user_id for m in members], state="active"))
    if (not room or not mapping or not mapping.matrix_room_id or len(identities) != 2 or not all(i.matrix_user_id for i in identities) or
            {room.user_one_id, room.user_two_id} != {m.user_id for m in members} or
            set(mapping.member_matrix_localparts) != {i.matrix_localpart for i in identities}):
        raise SparkConflict("Secure chat is getting ready. Open chat, then try Sparks again.")
    return room, mapping, {i.user_id: i.matrix_user_id for i in identities}


def session_for(user, spark_id, *, lock=False):
    initial = SparkSession.objects.filter(pk=spark_id, audience_user_ids__isnull=False).first()
    if not initial or user.pk not in initial.audience_user_ids:
        raise Http404
    couple, members = current_couple(user, initial.couple_id, lock=lock)
    spark = SparkSession.objects.select_for_update().get(pk=initial.pk) if lock else initial
    if spark.audience_membership_ids != [m.pk for m in members] or spark.audience_user_ids != [m.user_id for m in members]:
        raise Http404
    room, mapping, identities = room_for(couple, members)
    if str(room.pk) != str(spark.room_id) or mapping.membership_epoch != spark.membership_epoch or mapping.matrix_room_id != spark.matrix_room_id:
        raise Http404
    return spark, identities


@transaction.atomic
def start(user, request_id, couple_id, question_id):
    couple, members = current_couple(user, couple_id, lock=True)
    room, mapping, _ = room_for(couple, members)
    existing = SparkSession.objects.filter(pk=request_id).first()
    if existing:
        spark, _ = session_for(user, request_id)
        if spark.creator_id != user.pk or spark.couple_id != couple_id or spark.question_id != question_id:
            raise SparkConflict("This request was already used for a different Spark.")
        return spark, False
    # Old audiences can never resume or reveal an earlier relationship's answer.
    for stale in SparkSession.objects.filter(couple=couple, state="open"):
        if (stale.audience_membership_ids != [m.pk for m in members] or stale.matrix_room_id != mapping.matrix_room_id or stale.membership_epoch != mapping.membership_epoch):
            stale.answers.all().delete()
            stale.state = "skipped"
            stale.save(update_fields=["state"])
        else:
            raise SparkConflict("You already have a Spark together. Finish or skip it before starting another.")
    question = QUESTIONS[question_id]
    return SparkSession.objects.create(id=request_id, couple=couple, creator=user,
        audience_membership_ids=[m.pk for m in members], audience_user_ids=[m.user_id for m in members],
        room_id=room.pk, matrix_room_id=mapping.matrix_room_id, membership_epoch=mapping.membership_epoch,
        question_id=question_id, question=question["text"], deck=question["deck"]), True


@transaction.atomic
def submit(user, spark_id, envelope):
    spark, identities = session_for(user, spark_id, lock=True)
    if spark.state == "skipped":
        raise SparkConflict("This Spark was skipped. Your answer has not been revealed.")
    existing = spark.answers.filter(author=user).first()
    if existing:
        if existing.envelope != envelope:
            raise SparkConflict("Your answer was already submitted. Submitted answers cannot be edited.")
        return spark
    if spark.state != "open":
        raise SparkConflict()
    SparkAnswer.objects.create(spark=spark, author=user, sender_matrix_id=identities[user.pk], envelope=envelope)
    spark.revision += 1
    if spark.answers.count() == 2:
        spark.state = "revealed"
        spark.revealed_at = timezone.now()
    spark.save(update_fields=["revision", "state", "revealed_at"])
    return spark


@transaction.atomic
def skip(user, spark_id):
    spark, _ = session_for(user, spark_id, lock=True)
    if spark.state == "revealed":
        raise SparkConflict("Both answers are already open. This Spark cannot be withdrawn.")
    if spark.state != "skipped":
        spark.answers.all().delete()
        spark.state = "skipped"
        spark.revision += 1
        spark.save(update_fields=["state", "revision"])
    return spark


def data(user, spark):
    answers = list(spark.answers.order_by("author_id"))
    # The unrevealed partner envelope is omitted entirely, including on list/home.
    visible = [a for a in answers if spark.state == "revealed" or a.author_id == user.pk]
    return {"id": str(spark.pk), "couple": spark.couple_id, "creator": spark.creator_id,
        "room_id": str(spark.room_id), "matrix_room_id": spark.matrix_room_id,
        "membership_epoch": spark.membership_epoch, "question_id": spark.question_id,
        "question": spark.question, "deck": spark.deck, "state": spark.state, "revision": spark.revision,
        "created_at": spark.created_at, "revealed_at": spark.revealed_at, "card_event_id": spark.card_event_id,
        "submitted_by": [a.author_id for a in answers],
        "answers": [{"author": a.author_id, "sender_matrix_id": a.sender_matrix_id, "envelope": a.envelope} for a in visible]}
