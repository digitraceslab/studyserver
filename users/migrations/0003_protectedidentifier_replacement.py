import users.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_protectedidentifier'),
    ]

    operations = [
        migrations.AddField(
            model_name='protectedidentifier',
            name='replacement',
            field=models.CharField(default=users.models._generate_replacement, max_length=10),
        ),
    ]
