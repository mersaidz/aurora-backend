from rest_framework import serializers
from django.db import transaction
from django.utils import timezone

from users.models import UserProfile, User


def _calculate_age(birth_date):
    """
    Compute age in full years from a birth date. Returns None for falsy input.
    Same algorithm used by UserProfile.age, extracted here so the
    serializer can validate without instantiating a throwaway model object.
    """
    if not birth_date:
        return None
    today = timezone.now().date()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


class UserProfileSerializer(serializers.ModelSerializer):
    age = serializers.ReadOnlyField()

    class Meta:
        model = UserProfile
        fields = [
            'gender',
            'birth_date',
            'age',
            'height',
            'weight',
            'unit_system',
            'is_onboarded',
            'updated_at',
        ]
        read_only_fields = ['age', 'updated_at']

    def validate_birth_date(self, value):
        if not value:
            return value

        today = timezone.now().date()
        if value > today:
            raise serializers.ValidationError("Birth date cannot be in the future.")

        age = _calculate_age(value)
        if age is None:
            return value
        # 14+ is our legal floor — keeps us out of the under-13 data privacy
        # regime (COPPA in the US, similar carve-outs in EU/UK) by avoiding
        # data collection from minors at the most regulated tier.
        if age < 14:
            raise serializers.ValidationError(
                "Aurora is available for users aged 14 and above."
            )
        if age > 100:
            raise serializers.ValidationError("Please enter a valid birth date.")
        return value


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(required=False)

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'role',
            'date_joined',
            'profile',
        ]
        read_only_fields = ['id', 'email', 'role', 'date_joined']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Honestly, DRF is kinda weird here. It doesn't pass 'partial=True' to nested serializers
        # automatically. So if a user sends a PATCH request just to update their weight, 
        # the profile serializer will crash complaining about other missing required fields.
        #
        # I spent some time figuring this out, and this feels like a temporary workaround (or maybe not?), 
        # but forcing 'partial=True' directly into the profile field fixes the issue for now.
        if getattr(self, 'partial', False) and 'profile' in self.fields:
            self.fields['profile'].partial = True

    @transaction.atomic
    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', None)

        # Locking the user row to prevent concurrent PATCH requests from messing with get_or_create().
        # I guess select_for_update() should stop the parallel requests from blowing up on the OneToOne profile constraint.
        # Will test anyway
        User.objects.select_for_update().get(pk=instance.pk)

        instance = super().update(instance, validated_data)

        if profile_data is not None:
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()

        return instance

class RegisterUserSerializer(serializers.Serializer):
    #Public registration entrypoint.
    # Plain Serializer gives explicit control over what's accepted from the wire
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only = True,
        min_length = 8,
        style = {'input_type':'password'},
    )
    role = serializers.ChoiceField(
        choices = User.Role.choices,
        default = User.Role.ATHLETE,
        required = False,
    )

    def validate_email(self,value):
        normalized = User.objects.normalize_email(value)
        if User.objects.filter(email=normalized).exists():
            raise serializers.ValidationError("Account with this email already exists.")
        return normalized

    def validate_password(self,value):
        from django.contrib.auth.password_validation import validate_password
        validate_password(value)
        return value 
    
    def create(self,validated_data):
        from users.services.registration import register_user
        return register_user(**validated_data)