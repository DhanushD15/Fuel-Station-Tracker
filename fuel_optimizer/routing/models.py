from django.db import models


class FuelStation(models.Model):

    truckstop_id = models.IntegerField()

    name = models.CharField(max_length=255)

    address = models.CharField(max_length=255)

    city = models.CharField(max_length=100)

    state = models.CharField(max_length=50)

    rack_id = models.IntegerField()

    retail_price = models.FloatField()
    latitude = models.FloatField(null=True)
    longitude = models.FloatField(null=True)


    def __str__(self):

        return self.name
