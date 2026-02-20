$(document).ready(function() {
    if (window.location.pathname !== '/runs/start') {
        return;
    }

    var $startForm = $('form[action="/runs/start"]').has('input[name="action"][value="start_from_template"]').first();
    if ($startForm.length === 0) {
        return;
    }

    var storageKey = 'hedging.start_from_template.v1';
    var fieldDefaults = {
        template_id: '',
        total_quote: '1000',
        price_lower_pct: '5',
        price_upper_pct: '5'
    };

    function safeReadStorage() {
        try {
            var raw = localStorage.getItem(storageKey);
            if (!raw) {
                return {};
            }
            var parsed = JSON.parse(raw);
            if (parsed && typeof parsed === 'object') {
                return parsed;
            }
        } catch (e) {
        }
        return {};
    }

    function safeWriteStorage(payload) {
        try {
            localStorage.setItem(storageKey, JSON.stringify(payload));
        } catch (e) {
        }
    }

    function getFieldValue(name) {
        var $field = $startForm.find('[name="' + name + '"]').first();
        if ($field.length === 0) {
            return '';
        }
        return String($field.val() || '');
    }

    function setFieldValue(name, value) {
        var $field = $startForm.find('[name="' + name + '"]').first();
        if ($field.length === 0) {
            return;
        }
        if (name === 'template_id' && String(value).length > 0) {
            if ($field.find('option[value="' + String(value).replace(/"/g, '\\"') + '"]').length === 0) {
                return;
            }
        }
        $field.val(String(value));
    }

    function buildPayload() {
        return {
            template_id: getFieldValue('template_id'),
            total_quote: getFieldValue('total_quote'),
            price_lower_pct: getFieldValue('price_lower_pct'),
            price_upper_pct: getFieldValue('price_upper_pct')
        };
    }

    var stored = safeReadStorage();
    var initial = {
        template_id: (stored.template_id !== undefined && stored.template_id !== null) ? String(stored.template_id) : fieldDefaults.template_id,
        total_quote: (stored.total_quote !== undefined && stored.total_quote !== null) ? String(stored.total_quote) : fieldDefaults.total_quote,
        price_lower_pct: (stored.price_lower_pct !== undefined && stored.price_lower_pct !== null) ? String(stored.price_lower_pct) : fieldDefaults.price_lower_pct,
        price_upper_pct: (stored.price_upper_pct !== undefined && stored.price_upper_pct !== null) ? String(stored.price_upper_pct) : fieldDefaults.price_upper_pct
    };

    setFieldValue('template_id', initial.template_id);
    setFieldValue('total_quote', initial.total_quote);
    setFieldValue('price_lower_pct', initial.price_lower_pct);
    setFieldValue('price_upper_pct', initial.price_upper_pct);

    // Ensure defaults are persisted on first visit too.
    safeWriteStorage(buildPayload());

    $startForm.find('[name="template_id"],[name="total_quote"],[name="price_lower_pct"],[name="price_upper_pct"]').on('change input', function() {
        safeWriteStorage(buildPayload());
    });

    $startForm.on('submit', function() {
        safeWriteStorage(buildPayload());
    });
});
