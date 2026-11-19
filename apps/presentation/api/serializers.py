from rest_framework import serializers

from apps.kernel.errors import error_codes


class ErrorBodySerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=error_codes())
    message = serializers.CharField()
    fields = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
    request_id = serializers.CharField()


class ErrorEnvelopeSerializer(serializers.Serializer):
    error = ErrorBodySerializer()
