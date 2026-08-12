from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0001_build_orders_payments"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentProviderConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "provider",
                    models.CharField(
                        choices=[("inpay", "inPAY"), ("mirpay", "MirPay")],
                        max_length=20,
                        unique=True,
                    ),
                ),
                ("enabled", models.BooleanField(default=False)),
                ("is_default", models.BooleanField(default=False)),
                ("merchant_id", models.CharField(blank=True, max_length=50)),
                ("merchant_token_encrypted", models.TextField(blank=True)),
                ("callback_url", models.URLField(blank=True)),
                ("return_url", models.URLField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["provider"],
            },
        ),
    ]
