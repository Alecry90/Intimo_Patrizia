/* Chat same-origin. Identità e cookie sono gestiti dal server: nessun localStorage. */
(() => {
    'use strict';
    const el = id => document.getElementById(id);
    const panel = el('shop-chat');
    const toggle = el('chat-toggle');
    const identityForm = el('chat-identity-form');
    const conversation = el('chat-conversation');
    const messageInput = el('chat-message');
    const log = el('chat-log');
    const suggestions = Array.from(document.querySelectorAll('[data-chat-prompt]'));
    let customer = null;
    let connected = false;
    let initialized = false;
    let connecting = false;
    let signingIn = false;
    let busy = false;
    let pending = null;
    let categories = [];

    class ApiError extends Error {
        constructor(message, status) {
            super(message);
            this.status = status;
        }
    }

    async function api(path, options = {}) {
        if (window.location.protocol === 'file:') {
            throw new ApiError('La chat richiede il server Python.', 0);
        }
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 90000);
        try {
            const response = await fetch(path, {
                ...options,
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', ...options.headers },
                signal: controller.signal
            });
            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) {
                throw new ApiError('Questo indirizzo non espone il server della chat.', response.status);
            }
            const data = await response.json();
            if (!response.ok) {
                const detail = typeof data.detail === 'string' ? data.detail : data.error;
                throw new ApiError(typeof detail === 'string' ? detail : 'Il server non ha completato la richiesta.', response.status);
            }
            return data;
        } finally {
            window.clearTimeout(timeout);
        }
    }

    function status(text) {
        el('chat-status').textContent = text;
    }

    function setBusy(value) {
        busy = value;
        const blocked = value || Boolean(pending);
        messageInput.disabled = blocked;
        el('chat-send').disabled = blocked;
        suggestions.forEach(button => { button.disabled = blocked; });
        el('chat-delete').disabled = value;
        el('chat-retry').disabled = value;
        el('chat-reconnect').disabled = value;
        log.setAttribute('aria-busy', String(value));
    }

    function showConnectionProblem() {
        connected = false;
        el('chat-connection').hidden = false;
        el('chat-connection-text').textContent = 'Server della chat non raggiungibile. Avvia il server Python seguendo il README (python -m backend.server), poi apri http://127.0.0.1:8000. La chat non funziona aprendo index.html direttamente o con Live Server.';
    }

    function showIdentityError(text) {
        el('chat-identity-error').textContent = text;
        el('chat-identity-error').hidden = !text;
    }

    function safeCatalogLink(value) {
        if (typeof value !== 'string' || value.length > 128 || value !== value.trim()
            || !/^\/catalogo(?:\?categoria=[1-9]\d*(?:&prodotto=[1-9]\d*)?)?$/.test(value)) return null;
        const query = new URLSearchParams(value.split('?')[1] || '');
        if (Array.from(query.values()).some(id => !Number.isSafeInteger(Number(id)) || Number(id) <= 0)) return null;
        return value;
    }

    function addMessage(role, text, saved = false, links = []) {
        const article = document.createElement('div');
        article.className = `chat-bubble chat-bubble-${role}`;
        const label = document.createElement('strong');
        label.className = 'chat-message-label';
        label.textContent = role === 'user' ? 'Tu' : 'Assistente';
        const content = document.createElement('p');
        content.textContent = text;
        article.append(label, content);
        if (role === 'assistant' && Array.isArray(links)) {
            const group = document.createElement('div');
            group.className = 'chat-product-links';
            const seen = new Set();
            for (const item of links.slice(0, 12)) {
                const url = item && safeCatalogLink(item.url);
                if (!url || typeof item.label !== 'string' || !item.label.trim() || seen.has(url)) continue;
                seen.add(url);
                const link = document.createElement('a');
                link.href = url;
                link.textContent = item.label.slice(0, 180);
                group.append(link);
            }
            if (group.children.length) article.append(group);
        }
        if (saved) {
            const note = document.createElement('span');
            note.className = 'chat-save-note';
            note.textContent = 'Domanda salvata';
            article.append(note);
        }
        log.append(article);
        article.scrollIntoView({ block: 'nearest', behavior: 'auto' });
    }

    function renderPreferences(preferences) {
        const list = el('chat-preference-list');
        list.replaceChildren();
        const counts = preferences && preferences.category_counts || {};
        const entries = Object.entries(counts).filter(([, count]) => Number(count) > 0)
            .sort((a, b) => Number(b[1]) - Number(a[1]));
        for (const [id, count] of entries) {
            const category = categories.find(item => String(item.id) === id);
            const item = document.createElement('li');
            item.textContent = `${category ? category.name : `Categoria ${id}`}: ${count} ${Number(count) === 1 ? 'domanda' : 'domande'}`;
            list.append(item);
        }
        const productCounts = preferences && preferences.product_counts || {};
        for (const [id, count] of Object.entries(productCounts).filter(([, count]) => Number(count) > 0)) {
            const item = document.createElement('li');
            item.textContent = `ID prodotto ${id}: ${count} ${Number(count) === 1 ? 'domanda' : 'domande'}`;
            list.append(item);
        }
        if (!list.children.length) {
            const item = document.createElement('li');
            item.textContent = customer && customer.remember ? 'Nessun argomento salvato finora.' : 'Il salvataggio delle preferenze è disattivato.';
            list.append(item);
        }
    }

    async function loadPreferences() {
        const requestedCustomerId = customer && customer.id;
        const data = await api('/api/preferences');
        if (!customer || customer.id !== requestedCustomerId) return;
        categories = Array.isArray(data.categories) ? data.categories : [];
        renderPreferences(data.preferences);
    }

    function showCustomer(value) {
        const previousCustomerId = customer && customer.id;
        customer = value;
        identityForm.hidden = Boolean(value);
        conversation.hidden = !value;
        if (!value || (previousCustomerId && previousCustomerId !== value.id)) {
            pending = null;
            log.replaceChildren();
            el('chat-preference-list').replaceChildren();
            categories = [];
            el('chat-retry').hidden = true;
            messageInput.value = '';
            el('chat-customer').textContent = '';
            el('chat-memory-mode').textContent = '';
            status('');
            identityForm.reset();
            setBusy(false);
        }
        if (!value) return;
        el('chat-customer').textContent = `Ciao, ${value.name}`;
        el('chat-memory-mode').textContent = value.remember
            ? 'Salvataggio di domande e interessi attivo per questa sessione.'
            : 'Salvataggio di domande e interessi disattivato.';
        if (!log.children.length) {
            addMessage('assistant', 'Ciao! Chiedimi il prezzo, le taglie o i materiali di un prodotto, oppure esplora una categoria. Ad esempio: Quanto costa il Reggiseno Aurora? Il catalogo ? dimostrativo.');
        }
        renderPreferences(null);
        setBusy(false);
    }

    async function connect() {
        if (connecting || signingIn || busy) return;
        connecting = true;
        el('chat-reconnect').disabled = true;
        el('chat-start').disabled = true;
        try {
            const data = await api('/api/session');
            if (!Object.prototype.hasOwnProperty.call(data, 'customer')) throw new ApiError('Risposta del server non riconosciuta.', 0);
            connected = true;
            el('chat-connection').hidden = true;
            showCustomer(data.customer);
            if (customer) {
                try { await loadPreferences(); }
                catch { status('La sessione è attiva, ma non è stato possibile leggere le preferenze. Riapri la chat per riprovare.'); }
            }
            initialized = true;
        } catch {
            showConnectionProblem();
        } finally {
            connecting = false;
            el('chat-reconnect').disabled = false;
            el('chat-start').disabled = false;
        }
    }

    function openChat() {
        panel.hidden = false;
        toggle.setAttribute('aria-expanded', 'true');
        el('chat-close').focus();
        if (!initialized || !connected) void connect();
        else if (customer && !busy) void loadPreferences().catch(() => status('Non è stato possibile aggiornare le preferenze.'));
    }

    function closeChat() {
        panel.hidden = true;
        toggle.setAttribute('aria-expanded', 'false');
        toggle.focus();
    }

    async function sendPending() {
        if (!pending || busy) return;
        setBusy(true);
        el('chat-retry').hidden = true;
        status('L’assistente sta preparando la risposta…');
        try {
            const data = await api('/api/chat', { method: 'POST', body: JSON.stringify(pending) });
            if (typeof data.reply !== 'string') throw new ApiError('La risposta ricevuta non è valida.', 0);
            addMessage('assistant', data.reply, data.saved === true, data.links);
            if (data.preferences_available !== false) renderPreferences(data.preferences);
            const notices = [];
            if (data.status === 'human_required') notices.push('È necessario contattare il negozio: non è stata inviata alcuna richiesta allo staff.');
            if (data.status === 'clarification') notices.push('Servono più informazioni per proseguire.');
            if (data.llm_available === false) notices.push('LLM locale non disponibile: risposta basata sulle regole e sul catalogo.');
            if (data.preferences_available === false) notices.push('Preferenze temporaneamente non disponibili: resta visibile l’ultimo riepilogo letto.');
            const retrySave = data.status === 'human_required' && data.saved === false && customer && customer.remember;
            if (retrySave) notices.push('La domanda non è stata salvata. Puoi riprovare lo stesso invio quando il problema è risolto.');
            status(notices.join(' '));
            if (!retrySave) pending = null;
            el('chat-retry').hidden = !retrySave;
            connected = true;
            el('chat-connection').hidden = true;
        } catch (error) {
            if (error.status === 401) {
                pending = null;
                showCustomer(null);
                showIdentityError('La sessione è scaduta. Inserisci di nuovo nome ed email.');
            } else {
                const reason = error.name === 'AbortError' ? 'La risposta ha richiesto troppo tempo.'
                    : error instanceof ApiError ? error.message : 'Non è stato possibile ricevere la risposta.';
                status(`${reason} Puoi riprovare la stessa domanda: l’identificativo rimane invariato per evitare doppi salvataggi.`);
                el('chat-retry').hidden = false;
                if (!(error instanceof ApiError) || error.status === 404 || error.status === 0) showConnectionProblem();
            }
        } finally {
            setBusy(false);
            if (!pending && customer && !panel.hidden) messageInput.focus();
        }
    }

    function sendMessage(message) {
        if (busy || pending || !customer) return;
        const text = message.trim();
        if (!text) return;
        pending = { message: text, request_id: crypto.randomUUID() };
        addMessage('user', text);
        messageInput.value = '';
        void sendPending();
    }

    toggle.addEventListener('click', () => panel.hidden ? openChat() : closeChat());
    el('chat-close').addEventListener('click', closeChat);
    panel.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeChat();
        }
    });
    el('chat-reconnect').addEventListener('click', () => void connect());
    el('chat-retry').addEventListener('click', () => void sendPending());
    identityForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (connecting || signingIn || busy) return;
        showIdentityError('');
        const name = el('chat-name').value.trim();
        const email = el('chat-email').value.trim();
        if (!name || !email) {
            showIdentityError('Inserisci nome ed email.');
            return;
        }
        if (!identityForm.reportValidity()) return;
        signingIn = true;
        el('chat-start').disabled = true;
        try {
            const data = await api('/api/session', {
                method: 'POST',
                body: JSON.stringify({ name, email, remember: el('chat-remember').checked })
            });
            if (!data.customer) throw new ApiError('Dati di sessione mancanti.', 0);
            connected = true;
            initialized = true;
            el('chat-connection').hidden = true;
            showCustomer(data.customer);
            identityForm.reset();
            try { await loadPreferences(); }
            catch { status('La sessione è attiva, ma non è stato possibile leggere le preferenze.'); }
            messageInput.focus();
        } catch (error) {
            showIdentityError(error instanceof ApiError ? error.message : 'Impossibile avviare la chat. Controlla la connessione al server.');
            if (!(error instanceof ApiError) || error.status === 404 || error.status === 0) showConnectionProblem();
        } finally {
            signingIn = false;
            el('chat-start').disabled = false;
        }
    });
    el('chat-message-form').addEventListener('submit', event => {
        event.preventDefault();
        sendMessage(messageInput.value);
    });
    suggestions.forEach(button => button.addEventListener('click', () => sendMessage(button.dataset.chatPrompt)));
    el('chat-delete').addEventListener('click', async () => {
        if (busy) return;
        setBusy(true);
        status('Eliminazione dei dati in corso…');
        try {
            const result = await api('/api/session', { method: 'DELETE' });
            if (result.ok !== true) throw new ApiError('Cancellazione non confermata dal server.', 0);
            pending = null;
            log.replaceChildren();
            el('chat-preference-list').replaceChildren();
            el('chat-retry').hidden = true;
            status('');
            showCustomer(null);
            showIdentityError('');
            identityForm.reset();
            el('chat-name').focus();
        } catch {
            status('Impossibile eliminare i dati. Riprova: nessuna cancellazione è stata confermata.');
        } finally {
            setBusy(false);
        }
    });
})();
