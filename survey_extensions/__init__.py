"""Extends django-survey-and-report with conditional questions and "other" free-text.

Adds QuestionCondition (show a question only when a previous answer matches)
and QuestionExtra (adds an "Other, please specify" option to radio/select
questions), without forking the survey package.

Note: constructing ``Answer(question=..., body=other_text)`` directly (as
opposed to going through ``ExtendedResponseForm.save()``) will hit
``Answer.check_answer_body`` and raise, since the raw "other" text is not one
of the question's stored choices.
"""
