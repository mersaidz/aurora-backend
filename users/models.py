from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    
    #Default manager — sees ALL users including soft-deleted ones.
    #Used by Django auth, admin, and SimpleJWT so that a soft-deleted user
    #gets a clean "account deactivated" experience instead of "user does not exist".
    

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('role', 'ADMIN')

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)


class ActiveUserManager(UserManager):
    
    # Explicit manager that excludes soft-deleted users.
    # Business logic that should ignore accounts pending deletion uses
    # "User.active.filter(...)". Default "User.objects" is intentionally
    # left wide so Django auth/admin/JWT keep working correctly

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class User(AbstractUser):
    class Role(models.TextChoices):
        ATHLETE = 'ATHLETE', 'Athlete'
        COACH = 'COACH', 'Coach'
        ADMIN = 'ADMIN', 'Admin'

    username = None
    email = models.EmailField('email address', unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.ATHLETE)
    date_joined = models.DateTimeField('date joined', default=timezone.now, db_index=True)

    # Soft-delete marker. Set by "users.services.account_deletion.schedule_account_deletion".
    # The row stays visible to the default "objects" manager so auth/admin/JWT
    # can answer "account deactivated" instead of "user does not exist".
    # Business logic that should ignore deleted users queries through "User.active".
    # The actual hard delete happens later, asynchronously, in the Celery task.
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()        # sees all — for auth, admin, SimpleJWT
    active = ActiveUserManager()   # for business logic — excludes soft-deleted

    class Meta:
        ordering = ['-date_joined']

    def __str__(self):
        # Find out that its important to never expose full email in __str__: it leaks into Sentry, error pages,
        # repr(queryset) in logs, and debug pages. Mask local part, keep domain
        # for environment context (gmail vs corporate or others).
        if not self.email:
            return f"User #{self.pk}"
        local, sep, domain = self.email.partition('@')
        if not sep:
            return f"User #{self.pk}"
        if len(local) <= 2:
            masked_local = '*' * len(local)
        else:
            masked_local = f"{local[:2]}***"
        return f"User #{self.pk} ({masked_local}@{domain})"

    def get_display_name(self) -> str:
        
        # Full email for admin / internal UI use.
        # Do NOT call from log statements — use "str(user)"" for safe logging.
        
        return self.email or f"User #{self.pk}"


class AthleteProfile(models.Model):
    class UnitSystem(models.TextChoices):
        METRIC = 'METRIC', 'Metric (cm, kg)'
        IMPERIAL = 'IMPERIAL', 'Imperial (inches, lbs)'

    class Gender(models.TextChoices):
        MALE = 'MALE', 'Male'
        FEMALE = 'FEMALE', 'Female'
        OTHER = 'OTHER', 'Other'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='athlete_profile')
    gender = models.CharField(max_length=10, choices=Gender.choices, null=True, blank=True)
    birth_date = models.DateField('Birth Date', null=True, blank=True)

    height = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(50), MaxValueValidator(250)],
    )
    weight = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(300)],
    )

    # TECH DEBT: unit_system logically belongs on User (it's a UI/display
    # preference that affects every metric, not athlete-specific). Lives here
    # for now because moving requires a data migration and refactor of every
    # workout-processing code path that reads it. (Tracked in TECH_DEBT.md.) (don't forget)
    unit_system = models.CharField(
        max_length=10, choices=UnitSystem.choices, default=UnitSystem.METRIC,
    )
    is_onboarded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"Profile(user={self.user_id}, units={self.unit_system}, "
            f"onboarded={self.is_onboarded})"
        )

    @property
    def age(self):
        if not self.birth_date:
            return None
        today = timezone.now().date()
        return today.year - self.birth_date.year - (
            (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
        )
