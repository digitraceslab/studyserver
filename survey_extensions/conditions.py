from django.utils.text import slugify


def _as_slug_list(raw_value):
    """Normalize a raw parent answer value into a list of slugified choice values.

    ``raw_value`` may already be a list (select-multiple submitted data), or a
    single string, or a stringified list stored in ``Answer.body`` such as
    ``"[u'a', u'b']"`` (see ``Answer.values``).
    """
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple)):
        return [slugify(v) for v in raw_value if v not in (None, "")]
    raw_value = str(raw_value)
    if len(raw_value) >= 3 and raw_value[0:3] == "[u'":
        values = []
        raw_values = raw_value.split("', u'")
        nb_values = len(raw_values)
        for i, value in enumerate(raw_values):
            if i == 0:
                value = value[3:]
            if i + 1 == nb_values:
                value = value[:-2]
            values.append(value)
        return [slugify(v) for v in values if v not in (None, "")]
    return [slugify(raw_value)] if raw_value != "" else []


def evaluate(condition, raw_value):
    """Evaluate a QuestionCondition against the raw value of its parent answer.

    A missing/empty parent answer always evaluates to False (the child stays
    hidden until the parent is answered).
    """
    from survey_extensions.models import QuestionCondition

    if condition.operator == QuestionCondition.OP_IN:
        value_slugs = set(_as_slug_list(raw_value))
        if not value_slugs:
            return False
        condition_slugs = {slugify(label) for label in condition._clean_choice_labels()}
        return bool(value_slugs & condition_slugs)

    if raw_value in (None, ""):
        return False
    try:
        number = float(raw_value)
    except (TypeError, ValueError):
        return False

    if condition.number is None:
        return False

    if condition.operator == QuestionCondition.OP_EQ:
        return number == condition.number
    if condition.operator == QuestionCondition.OP_NE:
        return number != condition.number
    if condition.operator == QuestionCondition.OP_LT:
        return number < condition.number
    if condition.operator == QuestionCondition.OP_LE:
        return number <= condition.number
    if condition.operator == QuestionCondition.OP_GT:
        return number > condition.number
    if condition.operator == QuestionCondition.OP_GE:
        return number >= condition.number
    return False
