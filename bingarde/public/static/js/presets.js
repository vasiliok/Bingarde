/*
 * Saved filter presets in the build list's side panel.
 *
 *   [Nightly betas       v]   open it | Rename... / Subscribe / Delete...
 *   [new name     ][ok][x]   inline rename (Enter saves, Esc cancels)
 *   [del][Nightly ][ok][x]   delete confirmation
 *
 * Every change is a POST to /builds/filter/op or /builds/filter/save
 * (bingarde/controllers/builds.py), which answers with the full preset
 * list; the panel then redraws from it.
 *
 * Plain JS; the dropdown is Bootstrap 5's.
 */
(function() {
    'use strict';

    function esc(text) {
        var div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function fromHtml(html) {
        var tpl = document.createElement('template');
        tpl.innerHTML = html.trim();
        return tpl.content.firstChild;
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    function PresetList(element, options) {
        this.el = typeof element === 'string' ? document.querySelector(element) : element;
        this.opUrl = options.opUrl;                   // list / rename / delete / notify
        this.saveUrl = options.saveUrl;               // save a new preset
        this.href = options.href;                     // function(id) -> URL that opens a preset
        this.empty = options.empty || null;           // shown while there are no presets
        this.presets = [];
    }

    PresetList.prototype = {

        // ---- data -----------------------------------------------------------

        render: function(presets) {
            this.presets = presets || [];
            this.el.replaceChildren.apply(this.el, this.presets.map(this._item, this));
            if (this.empty) { this.empty.hidden = this.presets.length > 0; }
        },

        reload: function() {
            var self = this;
            return fetch(this.opUrl, {credentials: 'same-origin'})
                .then(function(r) { return r.json(); })
                .then(function(d) { self.render(d.payload); });
        },

        // req: {name, shared: 'all'|'me', filter: <filter tree or null>, sort: {field, order}}
        save: function(req) {
            return this._send(req, this.saveUrl);
        },

        _send: function(req, url) {
            var self = this;
            return fetch(url || this.opUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'X-CSRF-Token': csrfToken()},
                body: new URLSearchParams({json: JSON.stringify(req)})
            }).then(function(r) {
                if (!r.ok) { throw new Error(r.status + ' ' + r.statusText); }
                return r.json();
            }).then(function(d) {
                if (d.error) { alert(d.error); }
                self.render(d.payload || self.presets);
                return !d.error;
            }).catch(function(err) {
                alert(err.message);
                self.render(self.presets);
                return false;
            });
        },

        // ---- one preset -----------------------------------------------------

        // A link that opens the preset and a dropdown with its actions.
        _item: function(p) {
            var self = this;
            var li = fromHtml(
                '<li class="preset-item"><div class="btn-group w-100">' +
                '<a class="btn btn-outline-secondary btn-sm text-start text-truncate"></a>' +
                '<button type="button" class="btn btn-outline-secondary btn-sm dropdown-toggle dropdown-toggle-split flex-grow-0"' +
                ' data-bs-toggle="dropdown" aria-expanded="false"><span class="visually-hidden">Preset actions</span></button>' +
                '<ul class="dropdown-menu dropdown-menu-end">' +
                '<li><a href="#" class="dropdown-item" data-action="rename"><i class="bi bi-pencil me-2"></i>Rename...</a></li>' +
                // Only private presets (with an owner) can send e-mail.
                (p.user ? '<li><a href="#" class="dropdown-item" data-action="notify"><i class="bi ' +
                          (p.notify ? 'bi-bell-slash' : 'bi-bell') + ' me-2"></i>' +
                          (p.notify ? 'Unsubscribe' : 'Subscribe') + '</a></li>' : '') +
                '<li><hr class="dropdown-divider"></li>' +
                '<li><a href="#" class="dropdown-item text-danger" data-action="delete"><i class="bi bi-trash me-2"></i>Delete...</a></li>' +
                '</ul></div></li>');

            var open = li.querySelector('a.btn');
            open.href = this.href(p.id);
            open.title = p.name;
            open.innerHTML = (p.notify ? '<i class="bi bi-bell-fill text-primary me-1" title="E-mail notifications on"></i>' : '') +
                             esc(p.name);

            li.querySelectorAll('[data-action]').forEach(function(a) {
                a.addEventListener('click', function(e) {
                    e.preventDefault();
                    var action = a.getAttribute('data-action');
                    if (action === 'notify') {
                        self._send({action: 'notify', id: p.id});
                    } else {
                        self._edit(li, p, action);
                    }
                });
            });
            return li;
        },

        // Swap the item for an inline form: a name field (rename) or a confirmation (delete).
        _edit: function(li, p, mode) {
            var self = this;
            var rename = mode === 'rename';
            var form = fromHtml(
                '<form class="input-group input-group-sm">' +
                (rename ? '' : '<span class="input-group-text text-danger"><i class="bi bi-trash"></i></span>') +
                '<input type="text" class="form-control" maxlength="100" value="' + esc(p.name) + '"' +
                ' aria-label="' + (rename ? 'New preset name' : 'Preset to delete') + '"' + (rename ? '' : ' disabled') + '>' +
                '<button type="submit" class="btn btn-outline-success" aria-label="' + (rename ? 'Save name' : 'Delete') + '">' +
                '<i class="bi bi-check-lg"></i></button>' +
                '<button type="button" class="btn btn-outline-secondary" aria-label="Cancel"><i class="bi bi-x-lg"></i></button>' +
                '</form>');
            var field = form.querySelector('input');
            var buttons = form.querySelectorAll('button');

            var restore = function() { li.replaceWith(self._item(p)); };

            form.addEventListener('submit', function(e) {
                e.preventDefault();
                buttons.forEach(function(b) { b.disabled = true; });
                self._send(rename ? {action: 'rename', id: p.id, name: field.value}
                                  : {action: 'delete', id: p.id});
            });
            buttons[1].addEventListener('click', restore);
            form.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') { restore(); }
            });

            li.replaceChildren(form);
            if (rename) {
                field.focus();
                field.select();
            } else {
                buttons[0].focus();
            }
        }
    };

    window.PresetList = PresetList;
})();
