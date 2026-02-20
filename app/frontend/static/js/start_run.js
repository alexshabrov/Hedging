$(document).ready(function() {
    var path = String(window.location.pathname || '');
    if (!(path === '/runs/start' || path === '/runs/start/')) {
        return;
    }

    var $startForm = $('#start-run-form');
    if ($startForm.length === 0) {
        return;
    }

    var storageKey = 'hedging.start_from_template.v1';
    var fieldDefaults = {
        template_id: '',
        total_quote: '1000',
        price_lower_pct: '5',
        price_upper_pct: '5',
        dex_only: false,
        mock_source_dex: false
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
            var escaped = String(value).replace(/"/g, '\\"');
            if ($field.find('option[value="' + escaped + '"]').length === 0) {
                return;
            }
        }
        $field.val(String(value));
    }

    function getCheckboxValue(name) {
        var $field = $startForm.find('[name="' + name + '"]').first();
        if ($field.length === 0) {
            return false;
        }
        return Boolean($field.is(':checked'));
    }

    function setCheckboxValue(name, value) {
        var $field = $startForm.find('[name="' + name + '"]').first();
        if ($field.length === 0) {
            return;
        }
        $field.prop('checked', Boolean(value));
    }

    function buildPayload() {
        return {
            template_id: getFieldValue('template_id'),
            total_quote: getFieldValue('total_quote'),
            price_lower_pct: getFieldValue('price_lower_pct'),
            price_upper_pct: getFieldValue('price_upper_pct'),
            dex_only: getCheckboxValue('dex_only'),
            mock_source_dex: getCheckboxValue('mock_source_dex')
        };
    }

    function syncMockSourceControls() {
        var dexOnlyEnabled = getCheckboxValue('dex_only');
        var $mockSource = $startForm.find('[name="mock_source_dex"]').first();
        if ($mockSource.length === 0) {
            return;
        }
        if (!dexOnlyEnabled) {
            $mockSource.prop('checked', false);
        }
        $mockSource.prop('disabled', !dexOnlyEnabled);
    }

    var stored = safeReadStorage();
    var initial = {
        template_id: (stored.template_id !== undefined && stored.template_id !== null) ? String(stored.template_id) : fieldDefaults.template_id,
        total_quote: (stored.total_quote !== undefined && stored.total_quote !== null) ? String(stored.total_quote) : fieldDefaults.total_quote,
        price_lower_pct: (stored.price_lower_pct !== undefined && stored.price_lower_pct !== null) ? String(stored.price_lower_pct) : fieldDefaults.price_lower_pct,
        price_upper_pct: (stored.price_upper_pct !== undefined && stored.price_upper_pct !== null) ? String(stored.price_upper_pct) : fieldDefaults.price_upper_pct,
        dex_only: stored.dex_only === true,
        mock_source_dex: stored.mock_source_dex === true
    };

    setFieldValue('template_id', initial.template_id);
    setFieldValue('total_quote', initial.total_quote);
    setFieldValue('price_lower_pct', initial.price_lower_pct);
    setFieldValue('price_upper_pct', initial.price_upper_pct);
    setCheckboxValue('dex_only', initial.dex_only);
    setCheckboxValue('mock_source_dex', initial.mock_source_dex);
    syncMockSourceControls();

    safeWriteStorage(buildPayload());

    $startForm.find('[name="template_id"],[name="total_quote"],[name="price_lower_pct"],[name="price_upper_pct"],[name="dex_only"],[name="mock_source_dex"]').on('change input', function() {
        syncMockSourceControls();
        safeWriteStorage(buildPayload());
    });

    $startForm.on('submit', function() {
        safeWriteStorage(buildPayload());
    });
});
