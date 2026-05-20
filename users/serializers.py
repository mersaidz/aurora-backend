from rest_framework import serializers
from django.db import transaction 
from .models import User, AthleteProfile 

class AthleteProfileSerializer(serializers.ModelSerializer):
    age = serializers.ReadOnlyField()

    class Meta:
        model = AthleteProfile
        fields = [
            'gender',
            'birth_date',
            'age',
            'height',
            'weight',
            'unit_system',
            'is_onboarded',
            'updated_at'
        ]
        read_only_fields = ['age', 'updated_at']
    def validate_birth_date(self, value):
        if value:
            from .models import AthleteProfile
            temporary_profile = AthleteProfile(birth_date=value)
            age = temporary_profile.age
            if age is not None:
                if age < 10:
                    raise serializers.ValidationError("Athlete Profile is available for users aged 10 and above.")
                if age > 100:
                    raise serializers.ValidationError("Please enter a valid birth date.")
        return value

class UserSerializer(serializers.ModelSerializer):
    profile = AthleteProfileSerializer(source='athlete_profile', required=False)
    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'role',
            'date_joined',
            'profile'
        ]
        read_only_fields = ['id', 'email', 'role', 'date_joined']
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.method == 'PATCH':
            if 'profile' in self.fields:
                self.fields['profile'].required = False
                self.fields['profile'].partial = True
    

    def update(self, instance, validated_data):
        profile_data = validated_data.pop('athlete_profile', None)

        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if profile_data is not None:
                profile, created = AthleteProfile.objects.get_or_create(user=instance)
                for attr, value in profile_data.items():
                    setattr(profile, attr, value)
                profile.save()
            return instance 