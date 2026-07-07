from django.db import models


class WebhookLog(models.Model):
    endpoint = models.CharField(max_length=100)
    raw_body = models.TextField()
    verification_response = models.JSONField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    matched_order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.endpoint} @ {self.received_at.isoformat()}"
