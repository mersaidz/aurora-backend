from django.shortcuts import render
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from .models import User
from .serializers import UserSerializer, RegisterUserSerializer

class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    def get_queryset(self):
        return User.objects.select_related('profile').all()
    def get_object(self):
        queryset = self.get_queryset()
        obj = queryset.get(pk=self.request.user.pk)
        return obj

class RegisterView(generics.CreateAPIView):
    # POST api/auth/register/
    # Public endpoint, anybody can create account. Returns the created user (without password) and 201.
    # For the actual JWT tokens, the client follows up with POST /api/auth/token/.
    # Separating Register and token for any future email-verification gate.
    serializer_class = RegisterUserSerializer
    permission_classes = [AllowAny]
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            UserSerializer(user, context ={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )
