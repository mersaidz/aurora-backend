from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class SportType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    CATEGORY_CHOICES = [
        ('cardio', 'Cardio'),
        ('strength', 'Strength'),
        ('recovery', 'Recovery'),
        ('sport_specific', 'Sport Specific'),
    ]

    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    class Meta:
        ordering = ['name']
        verbose_name = 'Sport Type'
        verbose_name_plural = 'Sport Types'

    def __str__(self):
        return self.name

class Workout(models.Model):    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    sport_type = models.ForeignKey(SportType, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateTimeField(db_index=True)
    duration = models.DurationField(help_text="Format: HH:MM:SS (e.g., 01:30:00)")
    notes = models.TextField(blank=True)

    # Training load metrics
    rpe = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Rate of Perceived Exertion (1-10 scale), 1 = very light, 10 = max effort"
    )

    # Heart rate metrics
    avg_hr = models.PositiveSmallIntegerField(null=True, blank=True)
    max_hr = models.PositiveSmallIntegerField(null=True, blank=True)

    # Lactate measurement
    lactate = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True,
    validators=[MinValueValidator(0.1), MaxValueValidator(30.0)],
    help_text='Lactate concentration in mmol/L, e.g. 2.3, leave blank if not measured')

    #Calories burned
    calories = models.PositiveIntegerField(null=True, blank=True)

    #Training load calculation (TRIMP + AI insights)
    training_load = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True,
    validators=[MinValueValidator(0.0)],
    help_text='Calculated training load (TRIMP). For professional insights, lactate data analyzed by AI agent'
    ) 

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
            ordering = ['-date']
            indexes = [
                models.Index(fields=['user', 'date']),
            ]
    def __str__(self):
                return f"{self.user.email} - {self.date.strftime('%Y-%m-%d %H:%M')}"

# Create your models here.
