from django.views.generic import TemplateView
from django import forms
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class IndexView(LoginRequiredMixin, TemplateView):
    template_name = 'home/index.html'
    login_url = '/login/'


class DashboardView(TemplateView):
    template_name = 'home/dashboard.html'


class SignUpForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'input-custom',
            'placeholder': 'Ejemplo: correo@empresa.com',
            'autocomplete': 'email'
        })
    )

    username = forms.CharField(
        widget=forms.TextInput(attrs={
            'class': 'input-custom',
            'placeholder': 'Ejemplo: melany.vargas',
            'autocomplete': 'username'
        })
    )

    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'input-custom',
            'placeholder': 'Crea una contraseña segura',
            'autocomplete': 'new-password'
        })
    )

    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'input-custom',
            'placeholder': 'Repite tu contraseña',
            'autocomplete': 'new-password'
        })
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


def register_view(request):
    msg = None
    success = False

    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            form.save()
            success = True
            msg = 'Cuenta creada correctamente.'
        else:
            msg = 'Por favor corrige los errores del formulario.'
    else:
        form = SignUpForm()

    return render(request, 'accounts/register2.html', {
        'form': form,
        'msg': msg,
        'success': success
    })