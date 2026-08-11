from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model: login by email, username is a non-unique display name."""

    username = models.CharField(max_length=150)
    email = models.EmailField(max_length=254, unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.email
