from rest_framework.exceptions import APIException


class Conflict(APIException):
    status_code = 409
    default_detail = 'Дочекайся завершення обробки сесії.'
