from django.conf import settings
from django.db import models
from django.db.models import Q
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from .crypto import EncryptedTextField


class UserPhysioProfile(models.Model):
     METHOD_CHOICES = [
          ('formula', 'Formula 220-age'),
          ('karvonen', 'Karvonen Method'),
          ('lthr', 'Joe Friel LTHR'),
          ('manual', 'Manual'),
          ('ai', 'AI Calibrated') 
     ]
     user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='physio_profiles')
     created_at = models.DateTimeField(auto_now_add=True)
     is_active = models.BooleanField(default=True, db_index=True)

     method = models.CharField(max_length=15, choices=METHOD_CHOICES, default='formula')

     #basics
     max_hr = models.PositiveSmallIntegerField()
     rest_hr = models.PositiveSmallIntegerField(null=True, blank=True)
     threshold_hr = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Anaerobic threshold")
     ftp_watts = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Functional Threshold Power for cyclists")

     #HR zones in JSON format: {"zone1": {"min": 0, "max": 120}, "zone2": {"min": 121, "max": 140} and etc.}
     hr_zones = models.JSONField(default=dict, help_text="Heart rate zones based on the selected method")
     power_zones = models.JSONField(default=dict, blank=True, help_text="Power zones for cyclists and bike workouts")

     class Meta:
          ordering = ['-created_at']
          indexes = [
               models.Index(fields=['user', 'is_active', '-created_at']),
                     ]
     def __str__ (self):
          return f"PhysioProfile(User ID: {self.user_id}) — {self.get_method_display()} — {self.created_at:%Y-%m-%d}"

class SportType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subtypes'
    )
    category = models.CharField(max_length=50, choices=[
        ('cardio', 'Cardio'),
        ('strength', 'Strength'),
        ('recovery', 'Recovery'),
        ('specific', 'Sport Specific'),
    ])
    # API mapping for future integration: {"garmin": "running", "polar": "run_123"}
    external_mapping = models.JSONField(default=dict, blank=True)
    class Meta:
        ordering = ['name']
        verbose_name = 'Sport Type'
        verbose_name_plural = 'Sport Types'

    def __str__(self):
        return self.name
    
class DataSource(models.Model):
    PLATFORM_CHOICES = [
        ('whoop', 'Whoop'),
        ('oura', 'Oura Ring'),
        ('polar', 'Polar'),
        ('garmin', 'Garmin'),
        ('apple_health', 'Apple Health'),
        ('google_fit', 'Google Fit'),
        ('strava', 'Strava'),
        ('wahoo', 'Wahoo'),
        ('suunto', 'Suunto'),
        ('coros', 'Coros'),
        ('ultrahuman', 'Ultrahuman'),
        ('myfitnesspal', 'MyFitnessPal'),
        ('eight_sleep', 'Eight Sleep'),
        ('manual', 'Manual Entry'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    platform = models.CharField(max_length=50, choices = PLATFORM_CHOICES)
    is_active = models.BooleanField(default=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    access_token = EncryptedTextField(null=True, blank=True)
    refresh_token = EncryptedTextField(null=True, blank=True)
    token_expires = models.DateTimeField(null=True, blank=True)

    provider_user_id = models.CharField(
          max_length=255, blank=True, null=True,
          help_text = "User ID in provider system(e.g. Garmin Connect ID, Polar Flow ID)"
    )
    scopes = models.JSONField(default=list, blank=True, help_text="Oath scopes granted for this User while connecting")
    class Meta:
          constraints = [
               models.UniqueConstraint(
                    fields=['user', 'platform'],
                    name = 'unique_user_platform',
               ),
                models.UniqueConstraint(
                      fields=['platform', 'provider_user_id'],
                      condition= Q(provider_user_id__isnull=False),
                      name='unique_provider_user_id_per_platform'
                )
          ]

    def __str__ (self):
          return f"DataSource({self.id}) - {self.platform}"
    
    @property
    def is_token_valid(self):
          if not self.access_token or not self.token_expires:
                return False
          return timezone.now() < self.token_expires
    
    def has_scope(self, scope: str) -> bool:
          return scope in (self.scopes or [])
          
    def clear_tokens(self):
          self.access_token = None
          self.refresh_token = None
          self.token_expires = None
          self.provider_user_id = None
          self.scopes = []
          self.is_active = False
          self.save(update_fields=[
                'access_token', 'refresh_token', 'token_expires',
                  'provider_user_id', 'scopes', 'is_active'
          ])


class Workout(models.Model):
    VERIFICATION_LEVEL = [
          ('raw', 'Raw Sync'), # Data synced automatically from device/app
          ('verified', 'Verified by User'), # User/athlete confirmed, puts an RPE
          ('expert', 'Expert'), # Lactate is added, comment from AI Agent or coach viewed
          
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source = models.ForeignKey(DataSource, on_delete=models.SET_NULL, null=True, blank=True)
    sport_type = models.ForeignKey(SportType, on_delete=models.SET_NULL, null=True, blank=True)

    #Time, duration and status
    date = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(
         null=True, blank=True,
         help_text='need for checking duplicates by crossing time'
    )
    duration = models.DurationField(help_text="Format: HH:MM:SS (e.g., 01:30:00)")
    is_primary = models.BooleanField(default=True, db_index=True) #Duplicate detections<3
    duplicate_of =models.ForeignKey(
         'self', on_delete=models.SET_NULL,
         null=True, blank=True, related_name='duplicates'
    )
    verification_level = models.CharField(max_length=20, choices=VERIFICATION_LEVEL, default='raw')
    
    #general 
    avg_hr = models.PositiveSmallIntegerField(null=True, blank=True)
    max_hr = models.PositiveSmallIntegerField(null=True, blank=True)
    calories = models.PositiveIntegerField(null=True, blank=True)
    rpe = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="1 = very light, 10 = max effort"
    )
    feeling = models.CharField(max_length=255, blank=True) #subjective feeling 1-5

    #BIKE/RUN specific
    distance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Distance in kilometers")
    elevation_gain = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Elevation gain in meters")
    avg_speed = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Average speed in km/h")
    avg_pace = models.DurationField(null=True, blank=True, help_text="Average pace per kilometer, format: MM:SS (e.g., 05:30)")
    avg_power = models.PositiveSmallIntegerField(null=True, blank=True,) #Watts, cycling specific metrics
    avg_cadence = models.PositiveSmallIntegerField(null=True, blank=True) #RPM 
    tss = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True) #Training Stress Score, if available from device/app
    training_load = models.DecimalField(
          max_digits=7, decimal_places=2, null=True, blank=True,
          validators=[MinValueValidator(0.0)],
          help_text= 'Calculated training load (TSS/TRIMP) for AI color indicators'
    )
    training_load_calculated_at = models.DateTimeField(null=True, blank= True)

    # intensity detailed info
    hr_zones_data = models.JSONField(default=dict, blank=True, help_text="Time in HR zones (seconds)")
    power_zones_data = models.JSONField(default=dict, blank=True, help_text="Time in Power zones (seconds)")

    # our own Analytic indexes
    internal_stress_score = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Our own load calculation (Hybrid: HR + Power + RPE)"
    )
    variability_index = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="NP to Avg Power (how 'jerky' the training was)"
    )
    #Storage
    additional_metrics = models.JSONField(default=dict, blank=True) #pedal balance, outside temp and etc.
    raw_api_response = models.JSONField(default=dict, blank=True) #Store full API response from Garmin, Whoop or etc.

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
          ordering = ['-date']
          indexes = [models.Index(fields=['user', 'date', 'is_primary'])]
    def __str__(self):
          return f"Workout({self.sport_type_id}) - {self.date:%Y-%m-%d %H:%M}"

class LactateMeasurement(models.Model):
    workout = models.ForeignKey(Workout, on_delete=models.CASCADE, related_name='lactate_measurements')
    measured_at = models.DateTimeField(help_text="Date and time when lactate was measured, format: YYYY-MM-DD HH:MM")
    hr_bpm = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Heart rate in beats per minute at the time of lactate measurement")
    mmol = models.DecimalField(max_digits=4, decimal_places=2,
    validators=[MinValueValidator(0.1), MaxValueValidator(30.0)])
    class Meta:
         ordering = ['measured_at']
    def __str__(self):
            return f"LactateMeasurement({self.id}) - {self.mmol} mmol at {self.measured_at:%Y-%m-%d %H:%M}"


    #Everyday health insights(Whoop,Oura, Apple, Google Fit and etc.)
class HealthMetrics(models.Model):
    user =models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    source = models.ForeignKey('workouts.DataSource', on_delete=models.SET_NULL, null=True, blank=True)
    source_label = models.CharField(max_length=50, blank=True,)
    is_primary = models.BooleanField(default=True,) #Duplicate detections<3, prioritieze Whoop, Oura and etc.
    # Subjective morning feeling
    perceived_readiness = models.PositiveSmallIntegerField(
        null=True, blank=True, 
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Athlete self rate in the morning (1-10)"
    )
    
    #Sleep metrics
    sleep_duration = models.DurationField(null=True, blank=True, help_text="Total sleep duration, format: HH:MM:SS (e.g., 07:30:00)")
    sleep_score = models.PositiveSmallIntegerField(null=True, blank=True)
    recovery_score = models.PositiveSmallIntegerField(null=True, blank=True)
    hrv = models.PositiveSmallIntegerField(null=True, blank=True)
    rhr = models.PositiveSmallIntegerField(null=True, blank=True)
    #Sleep phases
    deep_sleep = models.DurationField(null=True, blank=True, help_text="Duration of deep sleep, format: HH:MM:SS (e.g., 02:00:00)")
    rem_sleep = models.DurationField(null=True, blank=True, help_text="Duration of REM sleep, format: HH:MM:SS (e.g., 01:30:00)")
    light_sleep = models.DurationField(null=True, blank=True, help_text="Duration of light sleep, format: HH:MM:SS (e.g., 04:00:00)")   
    awake_time = models.DurationField(null=True, blank=True, help_text="Duration of awake time during sleep, format: HH:MM:SS (e.g., 00:30:00)")
    sleep_consistency = models.PositiveSmallIntegerField(
          null=True,
          blank=True,
          validators=[MinValueValidator(0), MaxValueValidator(100)],
          help_text='Sleep consistency 1-100% (Whoop Consistency, Oura Regularity and etc.)'
    )
    #additional metrics
    respiratory_rate = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="Respiratory rate in breaths per minute")   
    skin_temp_delta = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="Skin temperature")
    spo2_avg = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(85.0), MaxValueValidator(100.0)],
        help_text='Average Blood Oxygen (%)' 
    )
    spo2_min = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='Min SP02 at night'
    )
    vo2max = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='VO2max ml/kg/min — Garmin/Polar estimate'  
    )
    class Meta:
         indexes = [models.Index(fields=['user', 'is_primary', 'date'])]
         constraints = [
              models.UniqueConstraint(
                   fields=['user', 'date', 'source'],
                   name='unique_user_date_source_healthmetrics'
              ),
              models.UniqueConstraint(
                   fields=['user', 'date'],
                   condition= Q(source__isnull=True),
                   name='unique_user_date_manual_entry'
              )
         ]
    def __str__(self):
         return f"HealthMetrics({self.id}) — {self.date}"

class AuditLog(models.Model):  
    ACTION_CHOICES = [
        ('token_refresh',     'Token Refreshed'),
        ('sync_success',      'Sync Success'),
        ('sync_failed',       'Sync Failed'),
        ('source_connect',    'Source Connected'),
        ('source_disconnect', 'Source Disconnected'),
        ('expert_edit',       'Expert Edit'),
        ('duplicate_resolved','Duplicate Resolved'),
    ]

    FORBIDDEN_KEYS = frozenset(['access_token', 'refresh_token', 'token_expires', 'password', 'token', 'secret', 'jwt', 'authorization'])

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action     = models.CharField(max_length=50, choices=ACTION_CHOICES)
    platform   = models.CharField(max_length=30, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    extra_info = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
             models.Index(fields=['user', '-created_at']),
             models.Index(fields=['action', '-created_at']),
             models.Index(fields=['ip_address', '-created_at']),
             ]
    def save(self, *args, **kwargs):
        if self.pk:
             raise PermissionError("AuditLog entries cannot be modified once created.")
        if self.extra_info:
             found = self.FORBIDDEN_KEYS & set(self.extra_info.keys())
             if found:
                  raise ValidationError(
                       f"Sensitive keys {found} are not allowed in extra_info for security reasons."
                  )
        super().save(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
         raise PermissionError("AuditLog entries cannot be deleted for security and compliance reasons.")

    def __str__(self):
        return f"AuditLog({self.action}) — {self.created_at:%Y-%m-%d %H:%M}"



    
