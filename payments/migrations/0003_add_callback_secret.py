from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_inpay_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentproviderconfig",
            name="callback_secret_encrypted",
            field=models.TextField(blank=True),
        ),
    ]
