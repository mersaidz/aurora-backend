from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from users.models import UserProfile, User


# Admin forms — we need custom forms because i made customuser(Abstract) with email instead username.
# (easy/modern sign in/up)
# With default Django UserChangeForm assume a "username" field exists, which would 500 on us.


class UserCreationFormEmail(forms.ModelForm):
    password1 = forms.CharField(label='Password', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Password (again)', widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ('email', 'role')

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords don't match.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class UserChangeFormEmail(forms.ModelForm):
    password = ReadOnlyPasswordHashField(
        label="Password",
        help_text=(
            "Raw passwords are not stored. ""Use the "
            "<a href=\"../password/\">change password form</a> to modify it."
        ),
    )

    class Meta:
        model = User
        fields = '__all__'



# Admin registrations


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    add_form = UserCreationFormEmail
    form = UserChangeFormEmail
    model = User

    # display_name uses the full email; ordinary Sentry/log paths use __str__ which masks.
    list_display = ('display_name', 'role', 'is_active', 'deleted_at', 'date_joined')
    list_filter = ('role', 'is_active', 'is_staff', 'is_superuser')
    search_fields = ('email',)
    ordering = ('-date_joined',)
    readonly_fields = ('last_login', 'date_joined', 'deleted_at')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name')}),
        ('Role', {'fields': ('role',)}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined', 'deleted_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'role', 'password1', 'password2'),
        }),
    )

    @admin.display(description='User', ordering='email')
    def display_name(self, obj):
        return obj.get_display_name()


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'gender', 'birth_date',
        'height', 'weight', 'unit_system', 'is_onboarded',
    )
    list_filter = ('gender', 'unit_system', 'is_onboarded')
    search_fields = ('user__email',)
    raw_id_fields = ('user',)
    readonly_fields = ('created_at', 'updated_at')
