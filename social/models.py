import uuid
from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from authentication.models import UserModel
from utils.choices import *
from datetime import timedelta
from django.utils import timezone

class CoupleConnectionModel(models.Model):
    sender_number = models.CharField(max_length=15)
    receiver_number = models.CharField(max_length=15)
    connection_status = models.CharField(
        max_length=20,
        choices=CoupleConnectionStatus,
        default=CoupleConnectionStatus.NOTHING,
    )
    breakup_reason = models.TextField(null=True, blank=True)
    # Acceptance is an idempotent relationship transition.  This UUID belongs to
    # the client operation that first committed the transition; legacy clients
    # may leave it null and still replay the already-accepted connection safely.
    request_operation_id = models.UUIDField(null=True, blank=True)
    acceptance_operation_id = models.UUIDField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # unique_together = [
        #     "sender_number",
        #     "receiver_number",
        # ]  # Prevent duplicate requests

        ordering = ["-updated_at"]
        verbose_name = "Couple_Connection"
        indexes = [
            models.Index(fields=["sender_number", "receiver_number","connection_status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["sender_number", "request_operation_id"],
                condition=models.Q(request_operation_id__isnull=False),
                name="unique_couple_request_operation",
            ),
        ]

    def __str__(self):
        return f"{self.sender_number}-{self.receiver_number}"


class CoupleModel(models.Model):
    couple_connection = models.OneToOneField(
        CoupleConnectionModel,
        on_delete=models.CASCADE,
        related_name="couple",
        null=True,
        blank=True
    )

    members = models.ManyToManyField(
        UserModel,
        through="CoupleMembershipModel",
        related_name="couple_profiles",
    )

    anniversary_date = models.DateField(null=True, blank=True)

    shared_dreams = models.JSONField(default=list, blank=True)
    shared_interests = models.JSONField(default=list, blank=True)

    title = models.CharField(max_length=100, null=True, blank=True)
    relationship_tagline = models.CharField(max_length=80, null=True, blank=True)
    journey_story = models.TextField(max_length=3000, null=True, blank=True)
    relationship_strength = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    cover_photo = models.ImageField(
        upload_to="couples/profile_private/",
        null=True,
        blank=True
    )

    cover_source_moment_photo = models.ForeignKey(
        "CoupleMomentPhotoModel",
        on_delete=models.SET_NULL,
        related_name="couple_profile_covers",
        null=True,
        blank=True,
    )
    cover_focal_y = models.FloatField(
        default=0.5,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    revision = models.PositiveBigIntegerField(default=1)
    section_revisions = models.JSONField(default=dict, blank=True)
    privacy_proposal = models.JSONField(null=True, blank=True)
    privacy_proposal_version = models.PositiveBigIntegerField(default=0)

    profile_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Couple"
        verbose_name_plural = "Couples"
        constraints = [models.CheckConstraint(condition=models.Q(cover_focal_y__gte=0, cover_focal_y__lte=1), name="couple_cover_focal_bounds")]

    def __str__(self):
        names = list(self.members.values_list("full_name", flat=True)[:2])
        return " ❤️ ".join(name for name in names if name) or f"Couple {self.pk}"


class ActiveCoupleMembershipQuerySet(models.QuerySet):
    def active(self):
        return self.filter(ended_at__isnull=True)


class ActiveCoupleMembershipManager(models.Manager.from_queryset(ActiveCoupleMembershipQuerySet)):
    """Security boundary: ordinary membership reads mean current membership."""

    def get_queryset(self):
        return super().get_queryset().active()


class CoupleMembershipModel(models.Model):
    class Position(models.IntegerChoices):
        PARTNER_ONE = 1, "Partner one"
        PARTNER_TWO = 2, "Partner two"

    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="couple_memberships",
    )
    position = models.PositiveSmallIntegerField(choices=Position.choices)
    nickname = models.CharField(max_length=30, null=True, blank=True)
    is_owner = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveCoupleMembershipManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["couple", "position"],
                name="unique_couple_member_position",
            ),
            models.UniqueConstraint(
                fields=["couple", "user"],
                name="unique_user_per_couple",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_active_couple_membership",
            ),
        ]
        indexes = [
            models.Index(fields=["couple", "position"]),
            models.Index(fields=["user", "ended_at"], name="couple_member_active_idx"),
        ]

    def __str__(self):
        return f"{self.user} in {self.couple_id}"


class CoupleStoryAnswerModel(models.Model):
    PROMPTS = [
        ("how_met", "Where did you two meet?"),
        ("first_move", "Who made the first move?"),
        ("first_impression", "First impression, in three words"),
    ]

    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="story_answers")
    author = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="couple_story_answers")
    prompt = models.CharField(max_length=32, choices=PROMPTS)
    answer = models.CharField(max_length=240)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["prompt", "author_id"]
        constraints = [
            models.UniqueConstraint(fields=["couple", "author", "prompt"], name="unique_couple_story_answer")
        ]
        indexes = [models.Index(fields=["couple", "prompt"], name="couple_story_prompt_idx")]


class CoupleSongModel(models.Model):
    couple = models.OneToOneField(CoupleModel, on_delete=models.CASCADE, related_name="song")
    title = models.CharField(max_length=120)
    artist = models.CharField(max_length=120, blank=True)
    band = models.CharField(max_length=120, blank=True)
    picked_by = models.ForeignKey(UserModel, on_delete=models.SET_NULL, null=True, related_name="picked_couple_songs")
    note = models.CharField(max_length=240, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class CouplePrivacyConsentModel(models.Model):
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="privacy_consents")
    user = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="couple_privacy_consents")
    proposal_version = models.PositiveBigIntegerField()
    approved_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["couple", "user"], name="unique_couple_privacy_consent")
        ]


class CoupleProfileChangeModel(models.Model):
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="profile_changes")
    actor = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="couple_profile_changes")
    section = models.CharField(max_length=32)
    action = models.CharField(max_length=20, default="updated")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["couple", "-created_at"], name="couple_profile_history_idx")]

class CouplePlanModel(models.Model):
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="plans")
    created_by = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="couple_plans")
    request_id = models.UUIDField()
    title = models.CharField(max_length=120)
    starts_at = models.DateTimeField()
    response = models.CharField(max_length=16, choices=[("pending", "Pending"), ("yes", "Yes"), ("no", "No"), ("another_time", "Another time")], default="pending")
    response_note = models.CharField(max_length=240, blank=True)
    completed = models.BooleanField(default=False)
    revision = models.PositiveBigIntegerField(default=1)
    audience_membership_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at", "id"]
        constraints = [models.UniqueConstraint(fields=["couple", "created_by", "request_id"], name="unique_couple_plan_request")]
        indexes = [models.Index(fields=["couple", "starts_at", "id"], name="couple_plan_date_idx")]


class CoupleStoryInviteModel(models.Model):
    """A durable profile prompt; delivery is intentionally not a chat side effect."""

    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="story_invites",
    )
    created_by = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="couple_story_invites",
    )
    request_id = models.UUIDField()
    prompt = models.CharField(max_length=32)
    audience_membership_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["couple", "created_by", "request_id"],
                name="unique_couple_story_invite_request",
            )
        ]


class CoupleFaveModel(models.Model):
    """A personal, one-way preference. Never shared with the user's partner."""

    user = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="couple_faves")
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="faves")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "couple"], name="unique_user_couple_fave")]
        indexes = [models.Index(fields=["user", "-created_at", "-id"], name="user_faves_order_idx")]


class CoupleMomentModel(models.Model):
    couple = models.ForeignKey(
        CoupleModel,
        on_delete=models.CASCADE,
        related_name="moments"
    )

    created_by = models.ForeignKey(
        UserModel,
        on_delete=models.CASCADE,
        related_name="created_couple_moments"
    )

    title = models.CharField(max_length=100, null=True, blank=True)

    caption = models.TextField(max_length=1000)

    moment_date = models.DateField()

    mood = models.CharField(
        max_length=30,
        choices=MomentMood.choices,
        null=True,
        blank=True
    )

    location_name = models.CharField(max_length=120, null=True, blank=True)

    visibility = models.CharField(
        max_length=20,
        choices=MomentVisibility.choices,
        default=MomentVisibility.PRIVATE
    )

    tags = models.JSONField(default=list, blank=True)

    partner_memory = models.TextField(
        max_length=1000,
        null=True,
        blank=True,
        help_text="Partner's perspective on this moment."
    )

    is_time_capsule = models.BooleanField(default=False)

    unlock_date = models.DateField(
        null=True,
        blank=True,
        help_text="If time capsule, moment unlocks on this date."
    )

    is_archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(editable=False, db_index=True)
    revision = models.PositiveBigIntegerField(default=1)
    # Snapshot membership identities, not just users: replacement partners never inherit access.
    audience_membership_ids = models.JSONField(default=list, editable=False)
    audience_user_ids = models.JSONField(default=list, editable=False)

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.created_at = timezone.now()
            self.expires_at = self.created_at + timedelta(hours=24)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["couple", "-created_at", "-id"], name="moment_couple_feed_idx"),
            models.Index(fields=["-created_at", "-id"], name="moment_feed_idx"),
        ]
        verbose_name = "Couple Moment"
        verbose_name_plural = "Couple Moments"

    def __str__(self):
        return f"{self.couple} - {self.title or self.moment_date}"

class CoupleMomentPhotoModel(models.Model):
    moment = models.ForeignKey(
        CoupleMomentModel,
        on_delete=models.CASCADE,
        related_name="photos"
    )

    image = models.ImageField(upload_to="couples/moments/")

    order = models.PositiveSmallIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"Photo for {self.moment}"


class CoupleMomentNoteModel(models.Model):
    moment = models.ForeignKey(CoupleMomentModel, on_delete=models.CASCADE, related_name="notes")
    sender = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="moment_notes")
    message = models.CharField(max_length=1000)
    recipient_membership_ids = models.JSONField(default=list, editable=False)
    recipient_user_ids = models.JSONField(default=list, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class MomentDraft(models.Model):
    """Private upload staging, never a feed item. Publication creates the existing Moment."""
    id = models.UUIDField(primary_key=True, editable=False)
    owner = models.ForeignKey(UserModel, on_delete=models.CASCADE)
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE)
    audience_membership_ids = models.JSONField(default=list)
    audience_user_ids = models.JSONField(default=list)
    revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True)
    moment = models.OneToOneField(CoupleMomentModel, on_delete=models.SET_NULL, null=True, related_name="publication_draft")
    publication_hash = models.CharField(max_length=64, blank=True)


class MomentDraftPhoto(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    draft = models.ForeignKey(MomentDraft, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="couples/moments/")
    digest = models.CharField(max_length=64)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)


class MomentReaction(models.Model):
    moment = models.ForeignKey(CoupleMomentModel, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(UserModel, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["moment", "user"], name="unique_moment_reaction")]


class MomentFileDeletion(models.Model):
    """Durable retry queue; created in the same transaction as photo deletion."""
    name = models.CharField(max_length=500, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class CoupleMomentNoteReplyModel(models.Model):
    note = models.ForeignKey(CoupleMomentNoteModel, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="moment_note_replies")
    message = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class CoupleMomentViewModel(models.Model):
    moment = models.ForeignKey(CoupleMomentModel, on_delete=models.CASCADE, related_name="views")
    # Retain aggregate history when a viewer deletes their account.
    viewer = models.ForeignKey(UserModel, on_delete=models.SET_NULL, null=True, related_name="moment_views")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["moment", "viewer"], name="unique_moment_viewer")]

# @deprecated("Use the new CoupleMomentPhotoModel instead.")
class PhotoAlbumModel(models.Model): #This model is now deprecated and will be removed in future releases. Please use CoupleMomentPhotoModel instead.
    couple = models.ForeignKey(
        CoupleModel, on_delete=models.CASCADE, related_name="couple_photo_album"
    )
    photo = models.ImageField(upload_to="couple_album/", null=True, blank=True)

    def __str__(self):
        return f"Photo for {self.couple}"

class SingleConnectionModel(models.Model):
    sender_number = models.CharField(max_length=15)
    receiver_number = models.CharField(max_length=15)
    connection_status = models.CharField(
        max_length=20,
        choices=SingleConnectionStatus,
        default=SingleConnectionStatus.NOTHING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:

        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["sender_number", "receiver_number","connection_status"]),
        ]

    def __str__(self):
        return f"{self.sender_number}-{self.receiver_number}"

class UserPreferenceModel(models.Model):
    user = models.OneToOneField(UserModel, on_delete=models.CASCADE, related_name="user_preferences")
    min_age = models.IntegerField(null=True, blank=True,default=18)
    max_age = models.IntegerField(null=True, blank=True,default=35)
    zodiac_sign = models.CharField(
        max_length=20, choices=ZodiacSignChoices, null=True, blank=True
    )
    radius_km = models.IntegerField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)    
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "User Preference"
        verbose_name_plural = "User Preferences"
        ordering = ["user"]
        indexes = [
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return self.user.full_name or self.user.phone_number


class SparkSession(models.Model):
    """Mutual-reveal gate. Answers contain only client-produced Matrix ciphertext."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="sparks")
    creator = models.ForeignKey(UserModel, on_delete=models.CASCADE, related_name="started_sparks")
    audience_membership_ids = models.JSONField()
    audience_user_ids = models.JSONField()
    room_id = models.UUIDField()
    matrix_room_id = models.CharField(max_length=255)
    membership_epoch = models.PositiveBigIntegerField()
    question_id = models.CharField(max_length=40)
    question = models.CharField(max_length=400)
    deck = models.CharField(max_length=16)
    state = models.CharField(max_length=12, default="open", choices=[("open", "Open"), ("revealed", "Revealed"), ("skipped", "Skipped")])
    revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    revealed_at = models.DateTimeField(null=True)
    card_event_id = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["couple"], condition=models.Q(state="open"), name="one_open_spark_per_couple")]
        indexes = [models.Index(fields=["couple", "-created_at"], name="spark_couple_created_idx")]


class SparkAnswer(models.Model):
    spark = models.ForeignKey(SparkSession, on_delete=models.CASCADE, related_name="answers")
    author = models.ForeignKey(UserModel, on_delete=models.CASCADE)
    sender_matrix_id = models.CharField(max_length=255)
    envelope = models.JSONField()
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["spark", "author"], name="one_answer_per_spark_author")]


class ChallengeTemplate(models.Model):
    """Versioned catalogue entry. Instances keep an immutable content snapshot."""
    id = models.CharField(primary_key=True, max_length=48)
    collection = models.CharField(max_length=24)
    content = models.JSONField()
    published = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]


class CoupleChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    couple = models.ForeignKey(CoupleModel, on_delete=models.CASCADE, related_name="challenges")
    creator = models.ForeignKey(UserModel, on_delete=models.CASCADE)
    template = models.ForeignKey(ChallengeTemplate, on_delete=models.PROTECT)
    snapshot = models.JSONField()
    audience_membership_ids = models.JSONField()
    audience_user_ids = models.JSONField()
    accepted_by = models.JSONField(default=list)
    confirmed_by = models.JSONField(default=list)
    paused_by = models.JSONField(default=list)
    state = models.CharField(max_length=12, default="invited", choices=[(s, s.title()) for s in
        ("invited", "active", "paused", "completed", "declined", "cancelled")])
    revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    card_event_id = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["couple"], condition=models.Q(state__in=["invited", "active", "paused"]), name="one_live_challenge_per_couple")]
        indexes = [models.Index(fields=["couple", "-created_at"], name="challenge_couple_created_idx")]


class ChallengeOperation(models.Model):
    """A durable retry receipt; never reapply an earlier pause after a resume."""
    challenge = models.ForeignKey(CoupleChallenge, on_delete=models.CASCADE, related_name="operations")
    author = models.ForeignKey(UserModel, on_delete=models.CASCADE)
    request_id = models.UUIDField()
    action = models.CharField(max_length=16)
    expected_revision = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["challenge", "author", "request_id"], name="unique_challenge_operation")]
