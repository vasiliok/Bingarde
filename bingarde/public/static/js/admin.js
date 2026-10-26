/* Admin pages: confirm deletes. On a group page, edit access rules with the
   build list's condition builder, and show how many builds the rule opens
   while it is edited (/admin/groups/preview). */
(function () {
    'use strict';

    document.querySelectorAll('[data-confirm]').forEach(function (button) {
        button.addEventListener('click', function (event) {
            if (!window.confirm(button.getAttribute('data-confirm'))) {
                event.preventDefault();
            }
        });
    });

    var form = document.getElementById('id-rule-form');
    if (!form || !window.ConditionBuilder) {
        return;
    }
    var page = JSON.parse(document.getElementById('id-page-data').textContent);
    var filterInput = document.getElementById('id-rule-filter');
    var ruleId = document.getElementById('id-rule-id');
    var level = document.getElementById('id-rule-level');
    var title = document.getElementById('id-rule-title');
    var submit = document.getElementById('id-rule-submit');
    var cancel = document.getElementById('id-rule-cancel');
    var preview = document.getElementById('id-rule-preview');
    var presetSelect = document.getElementById('id-rule-preset');

    function parse(text) {
        try { return text ? JSON.parse(text) : null; } catch (e) { return null; }
    }

    var fieldTypes = {};
    page.fields.forEach(function (f) { fieldTypes[f.name] = f.input; });
    // A failed submit comes back with its filter, so nothing typed is lost.
    var builder = new ConditionBuilder('#id-rule-builder', {
        fields: page.fields.map(function (f) { return [f.name, f.label]; }),
        fieldTypes: fieldTypes,
        preset: parse(filterInput.value),
        noun: 'Builds'
    });

    function setMode(rule) {
        ruleId.value = rule ? rule.id : '';
        form.action = form.getAttribute(rule ? 'data-save-action' : 'data-add-action');
        title.textContent = rule ? 'Edit rule' : 'New rule';
        submit.textContent = rule ? 'Save rule' : 'Add rule';
        cancel.hidden = !rule;
    }

    function ruleById(id) {
        return page.rules.filter(function (r) { return String(r.id) === String(id); })[0];
    }

    document.querySelectorAll('[data-edit-rule]').forEach(function (button) {
        button.addEventListener('click', function () {
            var rule = ruleById(button.getAttribute('data-edit-rule'));
            setMode(rule);
            level.value = rule.level;
            builder.setTree(rule.filter);
            form.scrollIntoView({behavior: 'smooth', block: 'center'});
        });
    });
    cancel.addEventListener('click', function () {
        setMode(null);
        builder.setTree(null);
    });
    if (presetSelect) {
        presetSelect.addEventListener('change', function () {
            var preset = page.presets.filter(function (p) { return String(p.id) === presetSelect.value; })[0];
            if (preset) { builder.setTree(preset.filter); }
            presetSelect.value = '';
        });
    }
    setMode(ruleById(ruleId.value) || null);

    form.addEventListener('submit', function (event) {
        var tree = builder.serialize();
        // A Public rule without conditions puts every build on the internet.
        if (!tree && form.hasAttribute('data-public') &&
                !window.confirm('This rule has no conditions: EVERY build, also nightlies and internal ones, ' +
                                'can be downloaded by anyone without signing in. Continue?')) {
            event.preventDefault();
            return;
        }
        filterInput.value = tree ? JSON.stringify(tree) : '';
    });

    // ---- live preview --------------------------------------------------------

    function text(tag, content, attrs) {
        var el = document.createElement(tag);
        el.textContent = content;
        Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
        return el;
    }

    var timer = null, asked = 0;
    function update() {
        var tree = builder.serialize();
        var q = tree ? JSON.stringify(tree) : '';
        var mine = ++asked;
        fetch('/admin/groups/preview?' + new URLSearchParams({q: q}).toString(), {credentials: 'same-origin'})
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (mine !== asked) { return; }
                preview.replaceChildren();
                if (data.error) {
                    preview.appendChild(text('span', data.error, {'class': 'text-danger'}));
                    return;
                }
                var line = (tree ? 'Matches ' : 'No conditions: every build, ') + data.count +
                    (data.count === 1 ? ' build' : ' builds');
                if (data.newest) {
                    line += ', newest ' + data.newest.name + ' (' + data.newest.branch + ')';
                }
                preview.appendChild(text('span', line + ' · '));
                preview.appendChild(text('a', 'Show them', {
                    href: tree ? '/builds/filter?' + new URLSearchParams({q: q}).toString() : '/builds',
                    target: '_blank', rel: 'noopener', id: 'id-rule-show'
                }));
            })
            .catch(function () { preview.replaceChildren(); });
    }
    $('#id-rule-builder').on('cb:change', function () {
        window.clearTimeout(timer);
        timer = window.setTimeout(update, 300);
    });
    update();
}());
