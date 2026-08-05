/*
 * Client-side show/hide for conditional questions and "other" free-text
 * fields. This is UX only: the server-side clean() in
 * survey_extensions.forms.ExtendedResponseForm is the source of truth
 * (no-JS users see every field; hidden/irrelevant answers are discarded
 * server-side).
 */
(function () {
    "use strict";

    function fieldValue(name) {
        var inputs = document.getElementsByName(name);
        if (!inputs.length) {
            // Parent question is not on this page (an earlier step): leave
            // the visibility decision to the server.
            return undefined;
        }
        var values = [];
        for (var i = 0; i < inputs.length; i++) {
            var input = inputs[i];
            var tag = input.tagName.toLowerCase();
            if (tag === "select") {
                for (var j = 0; j < input.options.length; j++) {
                    if (input.options[j].selected) {
                        values.push(input.options[j].value);
                    }
                }
            } else if (input.type === "checkbox" || input.type === "radio") {
                if (input.checked) {
                    values.push(input.value);
                }
            } else {
                values.push(input.value);
            }
        }
        return values;
    }

    function evaluateCondition(input) {
        var dependsOn = input.getAttribute("data-depends-on");
        if (!dependsOn) {
            return true;
        }
        var values = fieldValue(dependsOn);
        if (values === undefined) {
            return null; // unknown: leave server-decided visibility alone
        }
        var operator = input.getAttribute("data-operator");
        if (operator === "in") {
            var choices = (input.getAttribute("data-cond-choices") || "").split(",").filter(Boolean);
            for (var i = 0; i < values.length; i++) {
                if (choices.indexOf(values[i]) !== -1) {
                    return true;
                }
            }
            return false;
        }
        var raw = values[0];
        if (raw === undefined || raw === "") {
            return false;
        }
        var number = parseFloat(raw);
        if (isNaN(number)) {
            return false;
        }
        var target = parseFloat(input.getAttribute("data-cond-number"));
        if (isNaN(target)) {
            return false;
        }
        switch (operator) {
            case "eq":
                return number === target;
            case "ne":
                return number !== target;
            case "lt":
                return number < target;
            case "le":
                return number <= target;
            case "gt":
                return number > target;
            case "ge":
                return number >= target;
            default:
                return true;
        }
    }

    function clearInput(input) {
        if (input.type === "checkbox" || input.type === "radio") {
            input.checked = false;
        } else {
            input.value = "";
        }
    }

    function setRowHidden(input, hidden) {
        var row = input.closest("tr");
        if (!row) {
            return;
        }
        row.hidden = hidden;
        if (hidden) {
            clearInput(input);
        }
    }

    function updateConditionalFields() {
        var inputs = document.querySelectorAll("[data-depends-on]");
        // Iterate to a fixpoint so cascading conditions (a hidden parent
        // hiding its own child) settle correctly regardless of DOM order.
        var changed = true;
        var guard = 0;
        while (changed && guard < inputs.length + 1) {
            changed = false;
            guard += 1;
            for (var i = 0; i < inputs.length; i++) {
                var input = inputs[i];
                var visible = evaluateCondition(input);
                if (visible === null) {
                    continue;
                }
                var row = input.closest("tr");
                var currentlyHidden = row ? row.hidden : false;
                if (currentlyHidden === visible) {
                    setRowHidden(input, !visible);
                    changed = true;
                }
            }
        }
    }

    function updateOtherFields() {
        var others = document.querySelectorAll("[data-other-for]");
        for (var i = 0; i < others.length; i++) {
            var other = others[i];
            var parentName = other.getAttribute("data-other-for");
            var values = fieldValue(parentName);
            var isOther = values !== undefined && values.indexOf("__other__") !== -1;
            var row = other.closest("tr");
            if (row) {
                row.hidden = !isOther;
            }
            other.disabled = !isOther;
            if (!isOther) {
                other.value = "";
            }
        }
    }

    function updateAll() {
        updateConditionalFields();
        updateOtherFields();
    }

    document.addEventListener("DOMContentLoaded", function () {
        updateAll();
        document.addEventListener("change", updateAll);
        document.addEventListener("input", updateAll);
    });
})();
