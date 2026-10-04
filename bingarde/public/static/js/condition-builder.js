/*
 * Condition builder for the build list: groups of conditions.
 *
 *   [AND|OR]  Builds that match the following groups
 *   ┌ group ──────────────────────────────────────────┐
 *   │ (−) [Field ▾] [Condition ▾] [Value]          🗑 │
 *   │ (−) [Field ▾] [Condition ▾] [Value]             │
 *   │ (+ Add condition) │ [AND|OR] Builds that match … │
 *   └─────────────────────────────────────────────────┘
 *        (AND)
 *   ┌ group ... ┐
 *   (+ Add group)
 *
 * It reads and writes the filter tree the server understands
 * (bingarde/lib/filters.py):
 *   one group:      {all: [{field, op, value}, ...]}
 *   several groups: {any: [{all: [...]}, {any: [...]}, ...]}
 *   nothing:        null
 *
 * Needs jQuery.
 */
(function($) {
    'use strict';

    // Conditions and their value widget, per field type.
    var CONDITIONS = {
        'int': [
            ['eq', '=', 'number'], ['neq', '≠', 'number'],
            ['gt', '>', 'number'], ['gte', '≥', 'number'],
            ['lt', '<', 'number'], ['lte', '≤', 'number'],
            ['between', 'between', 'numberPair']
        ],
        'date': [
            ['eq', 'on', 'date'], ['between', 'between', 'datePair'],
            ['since', 'in the last, days', 'days'], ['older_than', 'older than, days', 'days']
        ],
        'lookup': [
            ['eq', 'is', 'lookup'], ['neq', 'is not', 'lookup']
        ],
        'text': [
            ['contains', 'contains', 'text'], ['eq', 'is', 'text'], ['neq', 'is not', 'text']
        ]
    };

    function esc(text) {
        return $('<div>').text(text == null ? '' : String(text)).html();
    }

    // The tree's "all"/"any" and the toggles' AND/OR.
    var JOIN_OP = {all: 'and', any: 'or'};
    var OP_JOIN = {and: 'all', or: 'any'};

    function joinOf(node) {
        if (!node || typeof node !== 'object') { return null; }
        return 'all' in node ? 'all' : 'any' in node ? 'any' : null;
    }

    // Every condition inside a node, however deep.
    function conditionsOf(node) {
        var join = joinOf(node);
        if (!join) { return node && node.field ? [node] : []; }
        return [].concat.apply([], (node[join] || []).map(conditionsOf));
    }

    /* True when the builder can show the tree exactly: conditions, or groups
       of conditions, joined at the top. */
    function fitsBuilder(tree) {
        var join = joinOf(tree);
        if (!join) { return true; }
        return (tree[join] || []).every(function(child) {
            var j = joinOf(child);
            return !j || (child[j] || []).every(function(c) { return !joinOf(c); });
        });
    }

    /* Filter tree -> {op, groups: [{op, conds: [...]}]}. Deeper nesting than
       the builder shows is flattened into one level of groups (see
       fitsBuilder: such a tree is kept as is until the user edits it). */
    function treeToGroups(tree) {
        var model = {op: 'and', groups: []};
        var join = joinOf(tree);
        if (!join) {
            if (tree && tree.field) { model.groups.push({op: 'and', conds: [tree]}); }
            return model;
        }
        var children = tree[join] || [];
        if (children.every(function(c) { return !joinOf(c); })) {
            model.groups.push({op: JOIN_OP[join], conds: conditionsOf(tree)});
            return model;
        }
        model.op = JOIN_OP[join];
        children.forEach(function(c) {
            var j = joinOf(c);
            model.groups.push({op: j ? JOIN_OP[j] : 'and', conds: conditionsOf(c)});
        });
        return model;
    }

    function ConditionBuilder(element, options) {
        this.$el = $(element);
        this.fields = options.fields;                 // [[name, label], ...]
        this.fieldTypes = options.fieldTypes;         // {name: 'int'|'date'|'lookup'|'text'}
        this.lookupUrl = options.lookupUrl || function(f) { return '/builds/values/' + encodeURIComponent(f); };
        this.noun = options.noun || 'builds';
        this._lookups = {};
        // A tree too deep to show: serialize() returns it unchanged until an edit.
        this._kept = fitsBuilder(options.preset) ? null : options.preset;
        var model = treeToGroups(options.preset);
        this.op = model.op;
        this._render();
        model.groups.forEach(function(g) { this.addGroup(g); }, this);
        this._refresh();
    }

    ConditionBuilder.prototype = {

        // ---- building blocks ------------------------------------------------

        _toggle: function(op, cls) {
            return $('<div class="cb-toggle" role="group">' +
                '<button type="button" data-op="and">AND</button>' +
                '<button type="button" data-op="or">OR</button></div>')
                .addClass(cls).attr('data-value', op)
                .find('button').each(function() {
                    $(this).toggleClass('active', $(this).data('op') === op)
                           .attr('aria-pressed', $(this).data('op') === op);
                }).end();
        },

        _render: function() {
            var self = this;
            this.$el.addClass('cb').empty();
            if (this._kept) {
                this.$notice = $('<div class="alert alert-warning cb-notice" role="status">' +
                    'This filter is nested deeper than the builder can show. It is applied as it is; ' +
                    'any change here replaces it with the simpler filter shown below.</div>').appendTo(this.$el);
            }
            this.$top = $('<div class="cb-top"></div>')
                .append(this._toggle(this.op, 'cb-top-toggle'))
                .append('<span class="cb-caption"></span>')
                .appendTo(this.$el);
            this.$groups = $('<div class="cb-groups"></div>').appendTo(this.$el);
            this.$empty = $('<p class="cb-empty">No conditions yet: all ' + esc(this.noun.toLowerCase()) +
                            ' are shown. Add a group to filter.</p>').appendTo(this.$el);
            this.$actions = $('<div class="cb-actions"></div>').appendTo(this.$el);
            $('<button type="button" class="cb-add-group"><i class="bi bi-plus-lg"></i>Add new group</button>')
                .appendTo(this.$actions);

            this.$el.on('click', '.cb-top-toggle button', function() {
                self.op = $(this).data('op');
                self._refresh();
            });
            // Any edit drops the kept tree (Save preset / Apply are not edits).
            this.$el.on('click', 'button', function() {
                if (!$(this).closest('.cb-actions-end').length) { self._edited(); }
            });
            this.$el.on('change input', 'input, select', function() { self._edited(); });
            this.$el.on('click', '.cb-add-group', function() {
                self.addGroup({op: 'and', conds: [{}]});
                self._refresh();
            });
        },

        _edited: function() {
            if (this._kept) {
                this._kept = null;
                this.$notice.remove();
            }
        },

        addGroup: function(group) {
            var self = this;
            var $group = $('<section class="cb-group" aria-label="Condition group"></section>');
            $group.data('op', group.op === 'or' ? 'or' : 'and');
            var $body = $('<div class="cb-group-body"><div class="cb-group-op"></div><div class="cb-rows"></div></div>')
                .appendTo($group);
            $('<button type="button" class="cb-delete-group" title="Delete group" aria-label="Delete group">' +
              '<i class="bi bi-trash3"></i></button>').appendTo($group);
            var $footer = $('<div class="cb-group-footer"></div>').appendTo($group);
            $('<button type="button" class="cb-add-cond"><i class="bi bi-plus-circle-fill"></i>Add condition</button>')
                .appendTo($footer);
            $('<div class="cb-group-match"></div>')
                .append(this._toggle($group.data('op'), 'cb-group-toggle'))
                .append('<span class="cb-caption">' + esc(this.noun) + ' that match the conditions above</span>')
                .appendTo($footer);

            this.$groups.append($('<div class="cb-link"><span class="cb-link-op"></span></div>')).append($group);

            $group.on('click', '.cb-group-toggle button', function() {
                $group.data('op', $(this).data('op'));
                self._refresh();
            });
            $group.on('click', '.cb-add-cond', function() {
                self.addRow($body.find('.cb-rows'), {});
                self._refresh();
            });
            $group.on('click', '.cb-delete-group', function() {
                $group.prev('.cb-link').remove();
                $group.remove();
                self._refresh();
            });

            var conds = group.conds && group.conds.length ? group.conds : [{}];
            conds.forEach(function(c) { self.addRow($body.find('.cb-rows'), c); });
            return $group;
        },

        addRow: function($rows, cond) {
            var self = this;
            var $row = $('<div class="cb-row"></div>').appendTo($rows);
            $('<button type="button" class="cb-remove" title="Remove condition" aria-label="Remove condition">' +
              '<i class="bi bi-dash-lg"></i></button>').appendTo($row);

            var $field = $('<select class="form-select cb-field" aria-label="Field"></select>').appendTo($row);
            this.fields.forEach(function(f) {
                $field.append($('<option>').val(f[0]).text(f[1]));
            });
            if (cond.field && this.fieldTypes[cond.field]) { $field.val(cond.field); }

            var $cond = $('<select class="form-select cb-cond" aria-label="Condition"></select>').appendTo($row);
            var $value = $('<div class="cb-value"></div>').appendTo($row);

            var fillConditions = function(selected) {
                var type = self.fieldTypes[$field.val()];
                $cond.empty();
                CONDITIONS[type].forEach(function(c) {
                    $cond.append($('<option>').val(c[0]).text(c[1]));
                });
                if (selected && $cond.find('option[value="' + selected + '"]').length) { $cond.val(selected); }
            };
            var fillValue = function(value) {
                var type = self.fieldTypes[$field.val()];
                var widget = CONDITIONS[type].filter(function(c) { return c[0] === $cond.val(); })[0][2];
                self._valueWidget($value.empty(), widget, $field.val(), value);
            };

            $field.on('change', function() { fillConditions(); fillValue(); });
            $cond.on('change', function() { fillValue(); });
            $row.on('click', '.cb-remove', function() {
                var $group = $row.closest('.cb-group');
                $row.remove();
                if (!$group.find('.cb-row').length) {
                    $group.prev('.cb-link').remove();
                    $group.remove();
                }
                self._refresh();
            });

            fillConditions(cond.op);
            fillValue(cond.value);
            return $row;
        },

        _valueWidget: function($box, widget, field, value) {
            var pair = $.isArray(value) ? value : [value, null];
            var number = function(placeholder, v) {
                return $('<input type="number" class="form-control">').attr('placeholder', placeholder).val(v == null ? '' : v);
            };
            var date = function(label, v) {
                return $('<input type="date" class="form-control cb-date">')
                    .attr('aria-label', label).val(v == null ? '' : v);
            };
            switch (widget) {
                case 'number': $box.append(number('value', value)); break;
                case 'text':
                    $box.append($('<input type="text" class="form-control">').attr('placeholder', 'value')
                                .val(value == null ? '' : value));
                    break;
                case 'days': $box.append(number('days', value)); break;
                case 'numberPair': $box.append(number('from', pair[0]), number('to', pair[1])); break;
                case 'date': $box.append(date('date', value)); break;
                case 'datePair': $box.append(date('from', pair[0]), date('to', pair[1])); break;
                case 'lookup':
                    var $select = $('<select class="form-select" aria-label="Value"><option value="">loading…</option></select>')
                        .appendTo($box);
                    this._lookup(field).done(function(list) {
                        $select.empty();
                        list.forEach(function(v) { $select.append($('<option>').val(v).text(v)); });
                        if (value != null && value !== '') {
                            if (!$select.find('option').filter(function() { return this.value === String(value); }).length) {
                                $select.append($('<option>').val(value).text(value));
                            }
                            $select.val(String(value));
                        }
                    });
                    break;
            }
        },

        _lookup: function(field) {
            if (!this._lookups[field]) {
                this._lookups[field] = $.getJSON(this.lookupUrl(field)).then(null, function() { return $.Deferred().resolve([]); });
            }
            return this._lookups[field];
        },

        // ---- state shown on screen ----------------------------------------------

        _setToggle: function($toggle, op) {
            $toggle.attr('data-value', op).find('button').each(function() {
                $(this).toggleClass('active', $(this).data('op') === op).attr('aria-pressed', $(this).data('op') === op);
            });
        },

        _refresh: function() {
            var self = this;
            var $groups = this.$groups.children('.cb-group');
            var opName = {and: 'all', or: 'any'};
            this._setToggle(this.$top.find('.cb-top-toggle'), this.op);
            this.$top.find('.cb-caption').text(
                $groups.length > 1 ? esc(this.noun) + ' that match ' + opName[this.op] + ' of the following groups'
                                   : esc(this.noun) + ' that match the following group');
            this.$top.toggleClass('cb-single', $groups.length < 2);
            this.$empty.toggle($groups.length === 0);
            this.$el.toggleClass('cb-has-groups', $groups.length > 0);

            this.$groups.children('.cb-link').each(function(i) {
                $(this).toggle(i > 0).find('.cb-link-op').text(self.op.toUpperCase());
            });
            $groups.each(function() {
                var $g = $(this), op = $g.data('op');
                var several = $g.find('.cb-row').length > 1;
                self._setToggle($g.find('.cb-group-toggle'), op);
                $g.toggleClass('cb-several', several);
                $g.find('.cb-group-op').text(op.toUpperCase());
                $g.find('.cb-group-match .cb-caption').text(
                    esc(self.noun) + ' that match ' + opName[op] + ' of the conditions above');
            });
        },

        // ---- output -------------------------------------------------------------------

        // {ok, value}: numbers for number fields, strings otherwise.
        _rowValue: function($row) {
            var inputs = $row.find('.cb-value input, .cb-value select');
            var vals = inputs.map(function() {
                var v = $.trim($(this).val() || '');
                return this.type === 'number' && v !== '' ? Number(v) : v;
            }).get();
            if (!vals.length || vals.some(function(v) { return v === ''; })) { return {ok: false}; }
            return {ok: true, value: vals.length === 1 ? vals[0] : vals};
        },

        /* The filter tree, or null when there is nothing to filter by. Rows
           with an empty value are left out. A kept deep tree comes back as is. */
        serialize: function() {
            if (this._kept) { return this._kept; }
            var self = this, groups = [];
            this.$groups.children('.cb-group').each(function() {
                var conds = [];
                $(this).find('.cb-row').each(function() {
                    var $row = $(this), v = self._rowValue($row);
                    if (!v.ok) { return; }
                    conds.push({field: $row.find('.cb-field').val(), op: $row.find('.cb-cond').val(), value: v.value});
                });
                if (conds.length) {
                    var group = {};
                    group[OP_JOIN[$(this).data('op')]] = conds;
                    groups.push(group);
                }
            });
            if (!groups.length) { return null; }
            if (groups.length === 1) { return groups[0]; }
            var tree = {};
            tree[OP_JOIN[this.op]] = groups;
            return tree;
        }
    };

    window.ConditionBuilder = ConditionBuilder;
    window.ConditionBuilder.treeToGroups = treeToGroups;
})(jQuery);
