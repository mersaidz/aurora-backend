from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator

class UserManager(BaseUserManager):
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

class User(AbstractUser):
    class Role(models.TextChoices):
         ATHLETE = 'ATHLETE', 'Athlete'
         COACH = 'COACH', 'Coach'
         ADMIN = 'ADMIN', 'Admin'

    username = None
    email = models.EmailField('email address', unique=True)
    role = models.CharField(max_length=15, choices=Role.choices, default=Role.ATHLETE)
    date_joined = models.DateTimeField('date joined', default=timezone.now, db_index=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    objects = UserManager()

    def __str__(self):
        return f"User({self.id})"

    class Meta:
            ordering =['-date_joined']

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

     height = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(50), MaxValueValidator(250)])
     weight = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(30), MaxValueValidator(300)])

     unit_system = models.CharField(max_length=10, choices=UnitSystem.choices, default=UnitSystem.METRIC)
     is_onboarded = models.BooleanField(default=False)
     created_at = models.DateTimeField(auto_now_add=True)
     updated_at = models.DateTimeField(auto_now=True)
     
     def __str__ (self):
          return f"Profile(USER ID: {self.user_id}, Units: {self.unit_system}, Onboarded: {self.is_onboarded})"
     
     @property
     def age(self) -> int:
          if not self.birth_date:
               return None
          today = timezone.now().date()
          return today.year - self.birth_date.year - (
               (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
          )
          



# Create your models here.
