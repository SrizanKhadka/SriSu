"""Product policy. No private copy or content belongs in notification metadata."""
from dataclasses import dataclass
from datetime import timedelta

CAPABILITY = "notification-catalogue-2"
CATEGORY_DEFAULTS = {
    "requests": True, "plans": True, "questions": True, "challenges": True,
    "moments": True, "moment_replies": True,
    "appreciations": False, "moment_reactions": False, "status": False,
    "ideas": False, "spark_progress": False, "challenge_progress": False,
}


@dataclass(frozen=True)
class Policy:
    category: str
    hours: int = 6
    audible: bool = False
    modern: bool = True


POLICIES = {
    "invite.received": Policy("requests", 24, True, False),
    "invite.accepted": Policy("requests", 6, True, False),
    "sharing.proposed": Policy("requests", 24, True, False),
    "sharing.approved": Policy("requests"),
    "plan.created": Policy("plans", 6, True, False),
    "plan.responded": Policy("plans", 6, True, False),
    "plan.cancelled": Policy("plans", 6, True),
    "spark.started": Policy("questions"),
    "spark.revealed": Policy("questions", 6, False, False),
    "spark.submitted": Policy("spark_progress"),
    "challenge.invited": Policy("challenges", 24, True),
    "challenge.responded": Policy("challenges"),
    "challenge.completed": Policy("challenges"),
    "challenge.cancelled": Policy("challenges"),
    "challenge.progress": Policy("challenge_progress"),
    "moment.published": Policy("moments"),
    "moment.hearted": Policy("moment_reactions"),
    "moment.appreciated": Policy("appreciations", 24),
    "moment.replied": Policy("moment_replies", 24),
    "status.changed": Policy("status", 1),
    "idea.created": Policy("ideas"),
    "idea.interested": Policy("ideas"),
}


def categories(pref):
    return CATEGORY_DEFAULTS | (pref.categories if pref else {})


def policy_expiry(kind, now):
    return now + timedelta(hours=POLICIES[kind].hours)
