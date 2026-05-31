# Signal receivers for the users app.

from __future__ import annotations
from django.db.models.signals import post_save
from django.dispatch import receiver
from users.models import AthleteProfile, User


@receiver(post_save, sender=User)
def ensure_athlete_profile(sender, instance, created, **kwargs) -> None:
    """
    Safety net: Automatically creates a blank AthleteProfile for new Users.
    
    Honestly, I'm not 100% sure if keeping this signal is the best long-term move. 
    Signals feel like dark magic — they run invisibly in the background, and it kinda 
    scares me because if we use User.objects.bulk_create() later, this will silently fail.
    
    But right now, it's a lifesaver for 'createsuperuser' and django-admin forms 
    so the app doesn't crash with RelatedObjectDoesNotExist on day one. 
    We'll probably clean this up later when I feel more confident about our registration service.
    """
    if created:
        AthleteProfile.objects.get_or_create(user=instance)

#LATER (TECH_DEBT.md):


