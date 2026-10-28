from django.conf import settings


def deployment(request):
    return {'server_mode': settings.DATABASES['default']['ENGINE'].endswith('postgresql')}
