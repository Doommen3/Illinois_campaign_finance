/**
 * Shared autocomplete/typeahead component.
 *
 * Usage:
 *   <input type="text" data-autocomplete-url="/527/suggest" ...>
 *
 * The component automatically attaches to every input with a
 * `data-autocomplete-url` attribute on DOMContentLoaded.
 *
 * Optional data attributes:
 *   data-autocomplete-min-chars  – minimum chars before fetching (default 2)
 *   data-autocomplete-limit      – max suggestions (default 10)
 *   data-autocomplete-param      – query param name sent to endpoint (default "q")
 *   data-autocomplete-value-field – if set, selecting a suggestion fills a
 *                                   hidden input with this name
 */
(function () {
  'use strict';

  var DEBOUNCE_MS = 200;
  var DEFAULT_MIN_CHARS = 2;
  var DEFAULT_LIMIT = 10;

  /* ---- helpers ---- */

  function normalizeQuery(raw) {
    if (!raw) return '';
    var text = raw.trim().toLowerCase();
    // Strip trailing dots from common abbreviations
    text = text.replace(/\b(inc|ltd|corp|co|assn|assoc|dept|gov|org|comm|natl|intl|jr|sr|dr|mr|mrs|ms|st|ave|blvd)\./gi, function (_, w) {
      return w.toLowerCase();
    });
    // Collapse whitespace
    text = text.replace(/\s+/g, ' ').trim();
    return text;
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function highlightMatch(text, query) {
    if (!query) return escapeHtml(text);
    var lowerText = text.toLowerCase();
    var lowerQuery = query.toLowerCase();
    var idx = lowerText.indexOf(lowerQuery);
    if (idx === -1) return escapeHtml(text);
    var before = text.slice(0, idx);
    var match = text.slice(idx, idx + query.length);
    var after = text.slice(idx + query.length);
    return escapeHtml(before) + '<mark>' + escapeHtml(match) + '</mark>' + escapeHtml(after);
  }

  function debounce(fn, ms) {
    var timer;
    return function () {
      var args = arguments;
      var ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  /* ---- core ---- */

  function initAutocomplete(input) {
    var url = input.getAttribute('data-autocomplete-url');
    if (!url) return;

    var minChars = parseInt(input.getAttribute('data-autocomplete-min-chars'), 10) || DEFAULT_MIN_CHARS;
    var limit = parseInt(input.getAttribute('data-autocomplete-limit'), 10) || DEFAULT_LIMIT;
    var paramName = input.getAttribute('data-autocomplete-param') || 'q';

    // Wrap the input in a positioned container
    var wrapper = document.createElement('div');
    wrapper.className = 'ac-wrapper';
    wrapper.setAttribute('role', 'combobox');
    wrapper.setAttribute('aria-expanded', 'false');
    wrapper.setAttribute('aria-haspopup', 'listbox');
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('autocomplete', 'off');

    // Create the listbox
    var listId = 'ac-list-' + Math.random().toString(36).slice(2, 8);
    var listbox = document.createElement('ul');
    listbox.id = listId;
    listbox.className = 'ac-listbox';
    listbox.setAttribute('role', 'listbox');
    listbox.style.display = 'none';
    wrapper.appendChild(listbox);
    input.setAttribute('aria-controls', listId);

    var activeIndex = -1;
    var items = [];
    var currentQuery = '';
    var abortController = null;

    function show() {
      if (items.length === 0) { hide(); return; }
      listbox.style.display = '';
      wrapper.setAttribute('aria-expanded', 'true');
    }

    function hide() {
      listbox.style.display = 'none';
      wrapper.setAttribute('aria-expanded', 'false');
      activeIndex = -1;
      setActive(-1);
    }

    function setActive(idx) {
      var lis = listbox.querySelectorAll('li');
      for (var i = 0; i < lis.length; i++) {
        lis[i].classList.remove('ac-active');
        lis[i].setAttribute('aria-selected', 'false');
      }
      if (idx >= 0 && idx < lis.length) {
        lis[idx].classList.add('ac-active');
        lis[idx].setAttribute('aria-selected', 'true');
        input.setAttribute('aria-activedescendant', lis[idx].id);
        // Scroll into view if needed
        lis[idx].scrollIntoView({ block: 'nearest' });
      } else {
        input.removeAttribute('aria-activedescendant');
      }
      activeIndex = idx;
    }

    function selectItem(item) {
      input.value = item.label;
      // If a hidden value field is configured, populate it with item.value
      var valueFieldName = input.getAttribute('data-autocomplete-value-field');
      if (valueFieldName) {
        var form = input.closest('form');
        if (form) {
          var hidden = form.querySelector('input[name="' + valueFieldName + '"]');
          if (hidden) {
            hidden.value = item.value || item.label;
          }
        }
        // Dispatch a custom event so JS listeners can react to selection
        input.dispatchEvent(new CustomEvent('autocomplete-select', {
          bubbles: true,
          detail: item,
        }));
      }
      hide();
      // Submit the form (unless a value field is set — let JS handle it)
      if (!valueFieldName) {
        var form = input.closest('form');
        if (form) {
          // For the flows page, trigger submit event instead of direct submit
          // so JS listeners (like the Sankey updater) can intercept it
          var evt = new Event('submit', { bubbles: true, cancelable: true });
          form.dispatchEvent(evt);
          if (!evt.defaultPrevented) {
            form.submit();
          }
        }
      }
    }

    function renderItems(data) {
      items = data;
      listbox.innerHTML = '';
      if (data.length === 0) { hide(); return; }

      for (var i = 0; i < data.length; i++) {
        var li = document.createElement('li');
        li.id = listId + '-opt-' + i;
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', 'false');
        li.className = 'ac-option';
        li.innerHTML = highlightMatch(data[i].label, currentQuery);
        li.setAttribute('data-index', i);
        li.addEventListener('mousedown', (function (idx) {
          return function (e) {
            e.preventDefault();
            selectItem(items[idx]);
          };
        })(i));
        li.addEventListener('mouseenter', (function (idx) {
          return function () { setActive(idx); };
        })(i));
        listbox.appendChild(li);
      }
      show();
    }

    function fetchSuggestions(query) {
      if (abortController) {
        abortController.abort();
      }
      abortController = typeof AbortController !== 'undefined' ? new AbortController() : null;

      var fetchUrl = url + '?' + encodeURIComponent(paramName) + '=' + encodeURIComponent(query) +
                     '&limit=' + limit;

      var opts = {};
      if (abortController) opts.signal = abortController.signal;

      fetch(fetchUrl, opts)
        .then(function (r) { return r.json(); })
        .then(function (data) { renderItems(data); })
        .catch(function () { /* aborted or network error */ });
    }

    var debouncedFetch = debounce(function () {
      var raw = input.value;
      var q = normalizeQuery(raw);
      currentQuery = q;
      if (q.length < minChars) { hide(); return; }
      fetchSuggestions(q);
    }, DEBOUNCE_MS);

    // Events
    input.addEventListener('input', debouncedFetch);

    input.addEventListener('keydown', function (e) {
      if (listbox.style.display === 'none') return;

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setActive(activeIndex < items.length - 1 ? activeIndex + 1 : 0);
          break;
        case 'ArrowUp':
          e.preventDefault();
          setActive(activeIndex > 0 ? activeIndex - 1 : items.length - 1);
          break;
        case 'Enter':
          if (activeIndex >= 0 && activeIndex < items.length) {
            e.preventDefault();
            selectItem(items[activeIndex]);
          }
          break;
        case 'Escape':
          e.preventDefault();
          hide();
          break;
      }
    });

    input.addEventListener('focus', function () {
      if (items.length > 0 && input.value.length >= minChars) {
        show();
      }
    });

    input.addEventListener('blur', function () {
      // Delay hide so mousedown on option can fire first
      setTimeout(hide, 150);
    });
  }

  /* ---- init ---- */

  function initAll() {
    var inputs = document.querySelectorAll('input[data-autocomplete-url]');
    for (var i = 0; i < inputs.length; i++) {
      initAutocomplete(inputs[i]);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
