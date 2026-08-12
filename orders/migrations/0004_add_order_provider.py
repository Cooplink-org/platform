from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0003_add_downloaded_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="provider",
            field=models.CharField(
                choices=[("mirpay", "MirPay"), ("inpay", "inPAY")],
                default="mirpay",
                max_length=20,
            ),
        ),
    ]
