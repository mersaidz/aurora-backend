from datetime import timedelta
from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from .crypto import EncryptedTextField
from .mixins import UserOwnedMixin
from .sanitize import find_forbidden_keys


class SportType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='subtypes'
    )
    category = models.CharField(max_length=50, choices=[
        ('cardio', 'Cardio'), # Run, bike, swim, hike, walk, etc.
        ('strength', 'Strength'), # Gym, weightlifting, crossfit, etc.
        ('flexibility', 'Flexibility/Mobility'), # Yoga, stretching, pilates, etc.
        ('specific', 'Sport Specific/Other'), # Martial arts, basketball, hockey, football and etc. that dont fit in other categories.
    ])
    # API mapping for future integration: {"garmin": "running", "polar": "run_123"}
    external_mapping = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Sport Type'
        verbose_name_plural = 'Sport Types'

    def __str__(self):
        return self.name


class UserPhysioProfile(models.Model):
    METHOD_CHOICES = [
        ('formula', 'Basic (5 zones) - based on age(220 - age)'),
        ('karvonen', 'Fitness (5 zones) - Carvonen method (max HR minus rest HR)'),
        ('lthr', 'Professional (7 zones) - based on Lactate Threshold(Joe Friel)'),
        ('manual', 'Custom (3-7 zones) - user manually enters zones'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='physio_profiles'
    )
    sport_type = models.ForeignKey(
        SportType, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Sport-specific profile (null = general fallback used when no sport-specific exists)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, db_index=True)

    method = models.CharField(max_length=15, choices=METHOD_CHOICES, default='formula')
    ai_adjustments_enabled = models.BooleanField(
        default=False,
        help_text="If True, allow AI offer recommendations to adjust zones based on workouts history and trends."
    )
    zone_count_setting = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(3), MaxValueValidator(7)],
        help_text="Target number of zones (3-7) to display in the UI, default = 5",
    )

    # Basics — sane HR ranges to catch bad data from buggy syncs / manual typos.
    max_hr = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(60), MaxValueValidator(250)],
    )
    rest_hr = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(25), MaxValueValidator(120)],
    )
    threshold_hr = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(80), MaxValueValidator(220)],
        help_text="Anaerobic threshold",
    )
    ftp_watts = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(50), MaxValueValidator(700)],
        help_text="Functional Threshold Power for cyclists",
    )

    # HR zones in JSON format: {"zone1": {"min": 0, "max": 120}, "zone2": {"min": 121, "max": 140}, ...}
    hr_zones = models.JSONField(default=dict, help_text="Heart rate zones based on the selected method")
    power_zones = models.JSONField(
        default=dict, blank=True,
        help_text="Power zones for cyclists and bike workouts, same format as hr_zones but with wattage thresholds instead of HR",
    )

    # Pro level specific user/athlete feature: if True, show lactate input in workout detail after
    # more accurate AI/coach feedback. Don't show for gen. users. UI should help enable this feature or not based on users needs.(f.e iron man or trianthetes or cyclists)
    lactate_testing_enabled = models.BooleanField(
        default=False,
        help_text="If True, show inline lactate input on workouts. Enable for pro/lab-tested athletes.",
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active', '-created_at']),
        ]
        constraints = [
            # One active profile per (user, sport_type). nulls_distinct=False so the NULL
            # if user is running,cycling and etc. He can have multiple profiles(ftp bike,hr run) but only one active per sport type.
            models.UniqueConstraint(
                fields=['user', 'sport_type'],
                condition=Q(is_active=True),
                nulls_distinct=False,
                name='unique_active_physio_per_user_sport',
            ),
        ]

    @property
    def zone_count(self) -> int:
        # Prefer the actual number of zones the user has configured.
        if isinstance(self.hr_zones, dict) and self.hr_zones:
            return len(self.hr_zones)
        # No explicit zones — fall back by method.
        if self.method == 'lthr':
            return 7
        return self.zone_count_setting  # formula / karvonen / unconfigured manual

    def clean(self):
        super().clean()
        if self.rest_hr is not None and self.rest_hr >= self.max_hr:
            raise ValidationError("Resting heart rate must be less than maximum heart rate.")

        # threshold_hr must sit between rest_hr and max_hr to be physiologically valid.
        if self.threshold_hr is not None:
            if self.threshold_hr >= self.max_hr:
                raise ValidationError({"threshold_hr": "Threshold HR must be less than maximum HR."})
            if self.rest_hr is not None and self.threshold_hr <= self.rest_hr:
                raise ValidationError({"threshold_hr": "Threshold HR must be greater than resting HR."})

        if self.method == 'manual' and self.hr_zones:
            count = len(self.hr_zones)
            if count < 3 or count > 7:
                raise ValidationError("Manual method requires between 3 and 7 heart rate zones.")

    def __str__(self):
        sport_hint = f"sport={self.sport_type_id}" if self.sport_type_id else "general"
        return f"PhysioProfile(user={self.user_id}, {sport_hint}) — {self.get_method_display()} — {self.created_at:%Y-%m-%d}"


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
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    is_active = models.BooleanField(default=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    access_token = EncryptedTextField(null=True, blank=True)
    refresh_token = EncryptedTextField(null=True, blank=True)
    token_expires = models.DateTimeField(null=True, blank=True)

    provider_user_id = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="User ID in provider system (e.g. Garmin Connect ID, Polar Flow ID)",
    )
    scopes = models.JSONField(
        default=list, blank=True,
        help_text="OAuth scopes granted for this user while connecting",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'platform'],
                name='unique_user_platform',
            ),
            models.UniqueConstraint(
                fields=['platform', 'provider_user_id'],
                condition=Q(provider_user_id__isnull=False),
                name='unique_provider_user_id_per_platform',
            ),
        ]

    def __str__(self):
        return f"DataSource({self.id}) - {self.platform}"

    @property
    def is_token_valid(self) -> bool:
        if not self.access_token or not self.token_expires:
            return False
        # 60-second skew buffer so a token that's about to expire isn't reported as valid
        # right before an outgoing request that would race the expiry.
        return timezone.now() + timedelta(seconds=60) < self.token_expires

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    def clear_tokens(self):       
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            locked.access_token = None
            locked.refresh_token = None
            locked.token_expires = None
            locked.provider_user_id = None
            locked.scopes = []
            locked.is_active = False
            locked.save(update_fields=[
                'access_token', 'refresh_token', 'token_expires',
                'provider_user_id', 'scopes', 'is_active',
            ])
        # Keep the caller's in-memory instance consistent with what's now in DB.
        self.refresh_from_db(fields=[
            'access_token', 'refresh_token', 'token_expires',
            'provider_user_id', 'scopes', 'is_active',
        ])


class Workout(UserOwnedMixin):
    VERIFICATION_LEVEL = [
        ('raw', 'Raw Sync'),           # synced automatically from device/app
        ('verified', 'Verified by User'),  # user/athlete confirmed, set RPE
        ('expert', 'Expert'),          # lactate added, coach/AI comment reviewed
    ]
    _owner_check_fields = ('source', 'user_physio_profile', 'duplicate_of')
    FEELING_CHOICES = [
        # use emojis in the UI for these, emojis + text for interface clarity, but store as integers for simplicity and sorting.
         (1, 'Terrible'),
         (2, 'Poor'),
         (3, 'Normal'),
         (4, 'Good'),
         (5, 'Great'),
     ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    user_physio_profile = models.ForeignKey(
        UserPhysioProfile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='workouts',
        help_text="Physiological profile of the user at the time of workout",
    )
    source = models.ForeignKey(DataSource, on_delete=models.SET_NULL, null=True, blank=True)
    sport_type = models.ForeignKey(SportType, on_delete=models.SET_NULL, null=True, blank=True)

    # ID from provider to help detect duplicates combined with source and time_window.
    external_id = models.CharField(max_length=255, blank=True, db_index=True)

    # Time, duration, dedup status
    date = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(
        null=True, blank=True,
        help_text='Needed for checking duplicates by overlapping time windows',
    )
    duration = models.DurationField(help_text="Format: HH:MM:SS (e.g., 01:30:00)")
    is_primary = models.BooleanField(default=True, db_index=True)
    duplicate_of = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='duplicates',
    )
    verification_level = models.CharField(
        max_length=20, choices=VERIFICATION_LEVEL, default='raw',
    )

    # General — validators clamp to sane human ranges to catch bad data/syncs/glytches.
    avg_hr = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(250)],
    )
    max_hr = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(250)],
    )
    calories = models.PositiveIntegerField(
        null=True, blank=True,
        validators=[MaxValueValidator(20000)],
    )
    rpe = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="1 = very light, 10 = max effort",
    )
    feeling = models.PositiveSmallIntegerField(
        choices=FEELING_CHOICES, null=True, blank=True,
        help_text="User's subjective mental/physical feeling about the workout (1-5, from terrible to great)",
    )

    # Bike / run specific
    distance = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Distance in kilometers",
    )
    elevation_gain = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Elevation gain in meters",
    )
    avg_speed = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Average speed in km/h",
    )
    avg_pace = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Average pace in seconds per kilometer",
    )
    avg_power = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MaxValueValidator(2000)],
        help_text="Average power in Watts",
    )
    avg_cadence = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MaxValueValidator(250)],
        help_text="RPM (cycling) or SPM (running)",
    )
    tss = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    training_load = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0.0)],
        help_text='Calculated training load (TSS/TRIMP) for AI color indicators',
    )
    training_load_calculated_at = models.DateTimeField(null=True, blank=True)

    # Intensity detailed info
    hr_zones_data = models.JSONField(
        default=dict, blank=True, help_text="Time in HR zones (seconds)",
    )
    power_zones_data = models.JSONField(
        default=dict, blank=True, help_text="Time in Power zones (seconds)",
    )

    # Our own analytic indixes
    internal_stress_score = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Our own load calculation (Hybrid: HR + Power + RPE)",
    )
    variability_index = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text="NP to Avg Power (how 'jerky' the training was)",
    )

    additional_metrics = models.JSONField(
        default=dict, blank=True,
        help_text="Structured extras: pedal balance, outside temp, etc.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        indexes = [models.Index(fields=['user', 'date', 'is_primary'])]
        constraints = [
            # DB-level dedup of repeated provider syncs.
            models.UniqueConstraint(
                fields=['source', 'external_id'],
                condition=~Q(external_id=''),
                name='unique_external_workout_per_source',
            ),
           #Hardest thing i tried to do in current migration. Basically catching silent bugs
           # via assigned FK with missspelled validated_data key so DB rejects bad row avoiding creating
           #an orphan non primary silently
            models.CheckConstraint(
                condition=(
                    Q(is_primary=True, duplicate_of__isnull=True)
                    | Q(is_primary=False, duplicate_of__isnull=False)
                ),
                name='workout_primary_state_consistent',
            ),
        ]

    def __str__(self):
        # never forget about BigO
        sport_hint = self.sport_type_id or "unknown"
        return f"Workout(id={self.pk}, sport={sport_hint}) - {self.date:%Y-%m-%d %H:%M}"


class WorkoutRawPayload(models.Model):
  #decided to move raw JSON to a separate table to keep the hot workout table lean and fast.
    workout = models.OneToOneField(
        Workout, related_name='raw_payload', on_delete=models.SET_NULL, null =True, blank = True,
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='raw_payloads')
    provider = models.CharField(max_length=30)
    provider_api_version = models.CharField(
        max_length=20, blank=True,
        help_text="Provider's API version at sync time (header echo or doc version)",
    )
    schema_version = models.CharField(
        max_length=20,
        help_text="Our parser version used when ingesting this payload",
    )
    payload = models.JSONField(
        help_text="Sanitized provider response (sensitive keys redacted via sanitize_payload)",
    )
    # Both fields below are duplicated from Workout so that an orphan payload
    # (workout=NULL after deletion) is still identifiable for restore flows.
    # blank=True because manual-entry workouts don't have provider-side ids
    provider_workout_id = models.CharField(
        max_length=255, blank=True, db_index=True,
        help_text="Copy of Workout.external_id for orphan identification",
    )
    workout_started_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="Copy of Workout.date for searching workout by timeline in bin",
    )
    payload_sha256 = models.CharField(max_length=64, db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['provider', 'received_at'])]
        constraints = [
            #Idempotency on user level, so group trainings will not blow up the DB(Strava mode on)
            models.UniqueConstraint(
                fields=['user', 'payload_sha256'],
                name = 'unique_payload_hash_per_user',
            ),
        ]

    def __str__(self):
        workout_hint = f"workout={self.workout_id}" if self.workout_id else f"ORPHAN({self.provider_workout_id})"
        return f"RawPayload({workout_hint}, user={self.user_id}, provider={self.provider})"


class LactateMeasurement(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lactate_measurements')
    workout = models.ForeignKey(
        Workout, on_delete=models.SET_NULL, null=True, blank=True, related_name='lactate_measurements',
    )
    measured_at = models.DateTimeField(
        help_text="Date and time when lactate was measured (YYYY-MM-DD HH:MM)",
    )
    hr_bpm = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Heart rate in bpm at the time of measurement",
    )
    mmol = models.DecimalField(
        max_digits=4, decimal_places=2,
        validators=[MinValueValidator(0.1), MaxValueValidator(30.0)],
    )

    class Meta:
        ordering = ['measured_at']

    def clean(self):
    # Validate lactate measurement time. Error if it's before workout but allow if it's after workout end time
    # (post-workout test or simply forgot to put measurments)
        super().clean()
        if self.workout_id and self.measured_at:
            workout = self.workout
            if self.measured_at < workout.date:
                raise ValidationError({
                    "measured_at": "Lactate measurement cannot precede workout start.",
                })

    def __str__(self):
        return f"LactateMeasurement({self.id}) - {self.mmol} mmol at {self.measured_at:%Y-%m-%d %H:%M}"


class HealthMetrics(UserOwnedMixin):
    #Everyday health insights (Whoop, Oura, Apple Health, Google Fit, etc.)
    _owner_check_fields = ('source',)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField(db_index=True)
    source = models.ForeignKey(
        'workouts.DataSource', on_delete=models.SET_NULL, null=True, blank=True,
    )
    source_label = models.CharField(max_length=50, blank=True)
    is_primary = models.BooleanField(default=True)  # prioritise Whoop/Oura/etc.

    # Sleep
    sleep_duration = models.DurationField(null=True, blank=True)
    sleep_score = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    recovery_score = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    hrv = models.PositiveSmallIntegerField(null=True, blank=True)
    rhr = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(25), MaxValueValidator(120)],
    )
    deep_sleep = models.DurationField(null=True, blank=True)
    rem_sleep = models.DurationField(null=True, blank=True)
    light_sleep = models.DurationField(null=True, blank=True)
    awake_time = models.DurationField(null=True, blank=True)
    sleep_consistency = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='Sleep consistency 1-100% (Whoop Consistency, Oura Regularity, etc.)',
    )

    respiratory_rate = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="Respiratory rate in breaths per minute",
    )
    skin_temp_delta = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="Skin temperature delta vs baseline",
    )
    spo2_avg = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(70.0), MaxValueValidator(100.0)],
        help_text='Average blood oxygen (%)',
    )
    spo2_min = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='Min SpO2 at night',
    )
    vo2max = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text='VO2max (ml/kg/min) — Garmin/Polar estimate',
    )

    class Meta:
        ordering = ['-date']
        indexes = [models.Index(fields=['user', 'is_primary', 'date'])]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'date', 'source'],
                name='unique_user_date_source_healthmetrics',
            ),
            models.UniqueConstraint(
                fields=['user', 'date'],
                condition=Q(source__isnull=True),
                name='unique_user_date_manual_entry',
            ),
            # Only one primary record per (user, date), across all sources.
            models.UniqueConstraint(
                fields=['user', 'date'],
                condition=Q(is_primary=True),
                name='unique_primary_health_per_user_date',
            ),
        ]

    def __str__(self):
        return f"HealthMetrics({self.id}) — user={self.user_id} date={self.date}"


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('token_refresh',     'Token Refreshed'),
        ('sync_success',      'Sync Success'),
        ('sync_failed',       'Sync Failed'),
        ('source_connect',    'Source Connected'),
        ('source_disconnect', 'Source Disconnected'),
        ('expert_edit',       'Expert Edit'),
        ('duplicate_resolved', 'Duplicate Resolved'),
        ('athlete_data_view', 'Athlete data viewed by coach'), 
        ('account_deletion_scheduled', 'Account Deletion Scheduled(GDPR 30 days window)') ,
        ('account_deletion_completed', 'Account Deletion Completed Permanently')
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
    )
    # Snapshots captured at write time so the audit row survives user deletion
    # (FK is SET_NULL — when the user is gone, user_id becomes NULL but these stay).
    # We keep BOTH:
    # user_id_snapshot — INT type, never lost, primary key for all logs by
    # this deleted user queries even after their row is gone.
    # user_email_snapshot — human-readable, useful in compliance reports.
    user_id_snapshot = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Snapshot of user.id at write time. Survives user deletion (SET_NULL above).",
    )
    user_email_snapshot = models.CharField(max_length=254, blank=True)

    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    platform = models.CharField(max_length=30, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    extra_info = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            # For querying after user deletion described above, keeping snapshot
            models.Index(fields=['user_id_snapshot', '-created_at']),
            models.Index(fields=['action', '-created_at']),
            models.Index(fields=['ip_address', '-created_at']),
        ]

    def save(self, *args, **kwargs):
        # Googled that _state.adding is more relible than
        # checking "self.pk" — it correctly handles cases where pk is set
        # before first save (cloning, manual id assignment, etc).
        if not self._state.adding:
            raise PermissionError("AuditLog entries cannot be modified once created.")

        # Recursive denylist — catches nested sensitive keys at any depth.
        if self.extra_info:
            hits = find_forbidden_keys(self.extra_info)
            if hits:
                raise ValidationError(
                    f"Sensitive keys found in extra_info at paths: {hits}. "
                    f"Pass the payload through sanitize_payload() before logging."
                )

        # Creating snapshot id's for security reasons. Resolving self.user can fail during deletion
        # or with a stale cache — we are fine with that and still write the log.
        if self.user_id:
            if self.user_id_snapshot is None:
                self.user_id_snapshot = self.user_id
            if not self.user_email_snapshot:
                email = ''
                try:
                    email = (self.user.email or '') if self.user else ''
                except Exception:
                    # User row deleted / non avialable — auditlog will be still saved
                    email = ''
                self.user_email_snapshot = email[:254]

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(
            "AuditLog entries cannot be deleted for security and compliance reasons."
        )

    def __str__(self):
        return f"AuditLog({self.action}) — {self.created_at:%Y-%m-%d %H:%M}"
