/* Catalogo pubblico: le informazioni arrivano dal medesimo backend della chat. */
(() => {
    'use strict';
    const el = id => document.getElementById(id);
    const grid = el('catalogue-products');
    const dialog = el('product-dialog');
    const search = el('catalogue-search-input');
    const money = new Intl.NumberFormat('it-IT', { style: 'currency', currency: 'EUR' });
    let categories = [];
    let products = [];
    let selectedCategory = null;
    let demoMode = false;
    let generation = 0;
    let opener = null;
    let ignoreNextClose = false;

    class CatalogueError extends Error {
        constructor(message, status = 0) { super(message); this.status = status; }
    }

    const isId = value => Number.isSafeInteger(value) && value > 0;
    const textValue = value => typeof value === 'string' && value.trim() ? value : null;
    const productName = product => textValue(product.name) || 'Nome non disponibile';
    const categoryName = product => textValue(product.category_name)
        || categories.find(category => category.id === product.category_id)?.name || 'Categoria non disponibile';
    const price = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? money.format(value) : 'Prezzo non disponibile';
    const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('it');
    const categoryUrl = id => id === null ? '/catalogo' : `/catalogo?categoria=${id}`;
    const productUrl = product => `/catalogo?categoria=${product.category_id}&prodotto=${product.id}`;

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function readRoute() {
        const params = new URLSearchParams(window.location.search);
        const result = { category: null, product: null };
        for (const [key, property] of [['categoria', 'category'], ['prodotto', 'product']]) {
            if (!params.has(key)) continue;
            const value = params.get(key);
            if (params.getAll(key).length !== 1 || !/^[1-9]\d*$/.test(value) || !isId(Number(value))) {
                throw new CatalogueError('Il collegamento al catalogo contiene una categoria o un prodotto non valido.', 422);
            }
            result[property] = Number(value);
        }
        return result;
    }

    async function api(path) {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(path, { signal: controller.signal, credentials: 'same-origin' });
            if (!(response.headers.get('content-type') || '').includes('application/json')) {
                throw new CatalogueError('Apri il sito dal server Python: questo indirizzo non espone il catalogo.');
            }
            const data = await response.json();
            if (!response.ok) throw new CatalogueError(typeof data.detail === 'string' ? data.detail : 'Il catalogo non è disponibile.', response.status);
            return data;
        } finally { window.clearTimeout(timer); }
    }

    function showState(message, retry = false, reset = false) {
        el('catalogue-state').hidden = !message;
        el('catalogue-state-text').textContent = message;
        el('catalogue-retry').hidden = !retry;
        el('catalogue-reset').hidden = !reset;
    }

    function ordinaryClick(event) {
        return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    }

    function renderCategories() {
        const nav = el('catalogue-categories');
        nav.replaceChildren();
        const choices = [{ id: null, name: 'Tutto' }, ...categories];
        for (const category of choices) {
            const link = element('a', 'catalogue-category', category.name);
            link.href = categoryUrl(category.id);
            if (category.id === selectedCategory) link.setAttribute('aria-current', 'page');
            link.addEventListener('click', event => {
                if (!ordinaryClick(event)) return;
                event.preventDefault();
                history.pushState(null, '', link.href);
                search.value = '';
                void load();
            });
            nav.append(link);
        }
    }

    function demoBadge() { return element('span', 'catalogue-demo-badge', 'Demo'); }

    function renderProducts() {
        const query = normalize(search.value.trim());
        const filtered = products.filter(product => normalize(`${productName(product)} ${categoryName(product)}`).includes(query));
        grid.replaceChildren();
        el('catalogue-count').textContent = `${filtered.length} ${filtered.length === 1 ? 'prodotto' : 'prodotti'}${query ? ' per la tua ricerca' : ''}`;
        showState(filtered.length ? '' : query ? 'Nessun prodotto corrisponde alla ricerca. Prova un altro nome.' : 'Questa categoria non contiene ancora prodotti.');
        for (const product of filtered) {
            const card = element('article', 'catalogue-product');
            const top = element('div', 'catalogue-product-top');
            top.append(element('p', 'catalogue-eyebrow', categoryName(product)));
            if (demoMode || product.is_demo === true) top.append(demoBadge());
            const heading = element('h2', 'catalogue-product-name');
            const link = element('a', '', productName(product));
            link.href = productUrl(product);
            const open = event => {
                if (!ordinaryClick(event)) return;
                event.preventDefault();
                openProduct(product, event.currentTarget);
            };
            link.addEventListener('click', open);
            heading.append(link);
            const description = element('p', 'catalogue-product-description', textValue(product.description) || 'Descrizione non disponibile.');
            const availability = element('p', 'catalogue-availability');
            availability.append(element('strong', '', 'Disponibilità: '), document.createTextNode(textValue(product.availability) || 'Non comunicata; contatta il negozio.'));
            const bottom = element('div', 'catalogue-product-bottom');
            const details = element('a', 'catalogue-detail-link', 'Apri la scheda');
            details.href = productUrl(product);
            details.setAttribute('aria-label', `Apri la scheda di ${productName(product)}`);
            details.addEventListener('click', open);
            bottom.append(element('p', 'catalogue-price', price(product.price_eur)), details);
            card.append(top, heading, description, availability, bottom);
            grid.append(card);
        }
    }

    function fieldText(value) {
        if (Array.isArray(value)) return value.length && value.every(item => textValue(item)) ? value.join(', ') : null;
        if (typeof value === 'string') return textValue(value);
        if (typeof value === 'number' && Number.isFinite(value)) return String(value);
        if (typeof value === 'boolean') return value ? 'Sì' : 'No';
        if (value && typeof value === 'object') return JSON.stringify(value);
        return null;
    }

    function openProduct(product, trigger = null, updateUrl = true) {
        opener = trigger;
        el('product-dialog-category').textContent = categoryName(product);
        const content = el('product-dialog-content');
        content.replaceChildren();
        const title = element('h2', '', productName(product));
        title.id = 'product-dialog-title';
        content.append(title, element('p', 'product-dialog-price', price(product.price_eur)));
        if (demoMode || product.is_demo === true) {
            const notice = element('p', 'catalogue-demo');
            notice.append(demoBadge(), document.createTextNode(` ${textValue(product.demo_notice) || 'Prodotto dimostrativo: prezzo e disponibilità sono fittizi.'}`));
            content.append(notice);
        }
        content.append(element('p', 'product-dialog-description', textValue(product.description) || 'Descrizione non disponibile.'));
        const fields = [
            ['brand', 'Marchio'], ['sizes', 'Taglie e misure'], ['colors', 'Colori'],
            ['materials', 'Materiali'], ['availability', 'Disponibilità']
        ];
        const omitted = new Set(['id', 'category_id', 'category_name', 'product_url', 'is_demo', 'demo_notice', 'name', 'description', 'price_eur', ...fields.map(([key]) => key)]);
        for (const key of Object.keys(product)) {
            if (!omitted.has(key)) fields.push([key, key.replace(/_/g, ' ')]);
        }
        const list = element('dl', 'product-details');
        for (const [key, label] of fields) {
            const row = element('div', 'product-detail-row');
            const value = fieldText(product[key]);
            row.append(element('dt', '', label), element('dd', value ? '' : 'product-detail-missing', value || 'Informazione non disponibile. Chiedi al negozio.'));
            list.append(row);
        }
        content.append(list, element('p', 'product-variant-note', 'Taglie e colori elencati descrivono il prodotto. La disponibilità delle singole combinazioni va verificata con il negozio.'));
        const contact = element('a', 'catalogue-detail-link', 'Chiedi al negozio · 080 579 4644');
        contact.href = 'tel:+390805794644';
        content.append(contact);
        if (updateUrl) history.replaceState(null, '', productUrl(product));
        if (!dialog.open) dialog.showModal();
        el('product-dialog-close').focus();
    }

    async function load() {
        const current = ++generation;
        if (dialog.open) {
            ignoreNextClose = true;
            dialog.close();
        }
        grid.replaceChildren();
        grid.setAttribute('aria-busy', 'true');
        search.disabled = true;
        el('catalogue-categories').replaceChildren();
        el('catalogue-count').textContent = '';
        showState('Caricamento del catalogo…');
        try {
            if (window.location.protocol === 'file:') throw new CatalogueError('Avvia il server Python e apri http://127.0.0.1:8000/catalogo per consultare i prodotti.');
            const route = readRoute();
            const [categoryData, productData] = await Promise.all([
                api('/api/catalog/categories'),
                api(`/api/catalog/products${route.category === null ? '' : `?category_id=${route.category}`}`)
            ]);
            if (current !== generation) return;
            if (!Array.isArray(categoryData.categories) || !Array.isArray(productData.products)
                || categoryData.categories.some(category => !isId(category.id) || !textValue(category.name))
                || productData.products.some(product => !isId(product.id) || !isId(product.category_id))) {
                throw new CatalogueError('Il server ha restituito un catalogo non valido. Riprova più tardi.');
            }
            categories = categoryData.categories;
            products = productData.products;
            selectedCategory = route.category;
            if (selectedCategory !== null && !categories.some(category => category.id === selectedCategory)) {
                throw new CatalogueError('La categoria richiesta non esiste nel catalogo.', 404);
            }
            demoMode = categoryData.demo_mode === true || productData.demo_mode === true;
            el('catalogue-demo').hidden = !demoMode;
            const name = categories.find(category => category.id === selectedCategory)?.name;
            el('catalogue-heading').textContent = name || 'Il nostro catalogo';
            document.title = `${name || 'Catalogo'} | Intimo Patrizia`;
            let product = null;
            if (route.product !== null) {
                const data = await api(`/api/catalog/products/${route.product}`);
                if (current !== generation) return;
                product = data.product;
                if (!product || product.id !== route.product || !isId(product.category_id)) throw new CatalogueError('La scheda prodotto ricevuta non è valida.');
                if (selectedCategory !== null && product.category_id !== selectedCategory) {
                    throw new CatalogueError('Questo prodotto non appartiene alla categoria indicata nel collegamento.', 422);
                }
            }
            renderCategories();
            renderProducts();
            search.disabled = false;
            if (product) openProduct(product, null, false);
        } catch (error) {
            if (current !== generation) return;
            const message = error.name === 'AbortError' ? 'Il caricamento sta impiegando troppo tempo. Riprova.'
                : error instanceof CatalogueError ? error.message : 'Non è stato possibile caricare il catalogo. Controlla la connessione e riprova.';
            products = [];
            grid.replaceChildren();
            el('catalogue-count').textContent = '';
            showState(message, !(error.status === 404 || error.status === 422), true);
        } finally {
            if (current === generation) grid.setAttribute('aria-busy', 'false');
        }
    }

    el('catalogue-retry').addEventListener('click', () => void load());
    search.addEventListener('input', renderProducts);
    el('product-dialog-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        if (ignoreNextClose) {
            ignoreNextClose = false;
            opener = null;
            return;
        }
        history.replaceState(null, '', categoryUrl(selectedCategory));
        if (opener && opener.isConnected) opener.focus();
        opener = null;
    });
    window.addEventListener('popstate', () => void load());
    void load();
})();
