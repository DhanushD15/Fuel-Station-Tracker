from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="fuelstation",
            name="latitude",
            field=models.FloatField(null=True),
        ),
        migrations.AddField(
            model_name="fuelstation",
            name="longitude",
            field=models.FloatField(null=True),
        ),
    ]
