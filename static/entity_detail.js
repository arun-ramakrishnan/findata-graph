// Entity Detail Page JavaScript
class EntityDetailPage {
    constructor() {
        this.entityPath = null;
        this.entity = null;
        this.currentImageIndex = 0;
        this.images = [];
        this.isFullscreen = false;
        
        this.init();
    }

    init() {
        // Get entity path from URL
        const pathParts = window.location.pathname.split('/');
        this.entityPath = pathParts.slice(2).join('/'); // Skip first two parts ('', 'entity')
        
        console.log('Initialized entity detail page with path:', this.entityPath);
        
        // Bind events
        this.bindEvents();
        
        // Load entity data
        this.loadEntity();
    }

    bindEvents() {
        // TOC toggle
        document.getElementById('toggle-toc').addEventListener('click', () => {
            this.toggleTableOfContents();
        });

        document.getElementById('toc-close').addEventListener('click', () => {
            this.hideTableOfContents();
        });

        // Export
        document.getElementById('export-content').addEventListener('click', () => {
            this.exportContent();
        });

        // Print
        document.getElementById('print-content').addEventListener('click', () => {
            this.printContent();
        });

        // Fullscreen
        document.getElementById('toggle-fullscreen').addEventListener('click', () => {
            this.toggleFullscreen();
        });

        // Lightbox
        document.getElementById('close-lightbox').addEventListener('click', () => {
            this.closeLightbox();
        });

        document.getElementById('image-lightbox').addEventListener('click', (e) => {
            if (e.target.id === 'image-lightbox') {
                this.closeLightbox();
            }
        });

        // Lightbox navigation
        document.getElementById('lightbox-prev').addEventListener('click', () => {
            this.navigateLightbox(-1);
        });

        document.getElementById('lightbox-next').addEventListener('click', () => {
            this.navigateLightbox(1);
        });

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            this.handleKeyboard(e);
        });

        // Handle fullscreen changes
        document.addEventListener('fullscreenchange', () => {
            this.updateFullscreenButton();
        });
    }

    async loadEntity() {
        try {
            console.log('Loading entity with path:', this.entityPath);
            this.showLoading(true);
            
            const apiUrl = `/api/entity/${this.entityPath}`;
            console.log('Fetching from API URL:', apiUrl);
            
            const response = await fetch(apiUrl);
            
            console.log('API Response status:', response.status);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const responseText = await response.text();
            console.log('Raw API Response length:', responseText.length);
            console.log('Raw API Response preview:', responseText.substring(0, 200));
            
            let entity;
            try {
                entity = JSON.parse(responseText);
            } catch (parseError) {
                console.error('JSON Parse Error:', parseError);
                this.showError('Invalid response from server');
                return;
            }
            
            console.log('Parsed Entity data:', entity);
            
            if (entity.error) {
                console.error('Entity API error:', entity.error);
                this.showError(entity.error);
                return;
            }

            this.entity = entity;
            this.displayEntity();
            
        } catch (error) {
            console.error('Error loading entity:', error);
            this.showError(`Failed to load entity: ${error.message}`);
        }
    }

    displayEntity() {
        // Update page title. Assignment to .textContent is itself safe (no XSS),
        // so we use the raw name directly rather than the unsafe htmlDecode().
        const entityName = this.entity.name || 'Entity';
        document.title = `${entityName} - FinData Knowledge Graph`;
        document.getElementById('page-title').textContent = entityName;

        // Update breadcrumb
        this.updateBreadcrumb();

        // Display metadata
        this.displayMetadata();

        // Process and display content
        this.displayContent();

        // Hide loading, show content
        this.showLoading(false);
        document.getElementById('main-content').style.display = 'flex';
    }

    updateBreadcrumb() {
        const breadcrumbCurrent = document.getElementById('breadcrumb-current');
        const entityType = this.entity.entity_type || 'entity';
        // .textContent is safe; no htmlDecode needed.
        breadcrumbCurrent.textContent = `${entityType.charAt(0).toUpperCase() + entityType.slice(1)}: ${this.entity.name}`;
    }

    displayMetadata() {
        const metadataContainer = document.getElementById('entity-metadata');
        const tags = this.entity.enhanced_tags || [];

        // Create DOM elements to safely insert content
        const container = document.createElement('div');
        container.className = 'entity-header-content';

        const titleSection = document.createElement('div');
        titleSection.className = 'entity-title-section';

        const titleElement = document.createElement('h1');
        titleElement.textContent = this.entity.name;
        titleSection.appendChild(titleElement);

        const typeBadge = document.createElement('div');
        typeBadge.className = `entity-type-badge ${this.escapeHtml(this.entity.entity_type || 'entity')}`;
        typeBadge.innerHTML = `<i class="fas fa-${this.getEntityIcon(this.entity.entity_type)}"></i> ${this.escapeHtml(this.entity.entity_type || 'Entity')}`;
        titleSection.appendChild(typeBadge);

        container.appendChild(titleSection);

        const metaGrid = document.createElement('div');
        metaGrid.className = 'entity-meta-grid';

        // Create row 1: sector, created, last modified
        const row1 = document.createElement('div');
        row1.className = 'entity-meta-grid';

        // Add sector classification if available
        if (this.entity.sector_classification) {
            const sectorItem = document.createElement('div');
            sectorItem.className = 'meta-item';
            sectorItem.innerHTML = `<i class="fas fa-industry"></i> <span class="meta-label">Sector:</span> <span class="meta-value">${this.escapeHtml(this.entity.sector_classification)}</span>`;
            row1.appendChild(sectorItem);
        }

        // Add created date if available
        if (this.entity.frontmatter && this.entity.frontmatter.created) {
            const createdItem = document.createElement('div');
            createdItem.className = 'meta-item';
            createdItem.innerHTML = `<i class="fas fa-calendar-plus"></i> <span class="meta-label">Created:</span> <span class="meta-value">${this.escapeHtml(this.entity.frontmatter.created)}</span>`;
            row1.appendChild(createdItem);
        }

        // Add last modified date if available
        if (this.entity.frontmatter && this.entity.frontmatter.last_modified) {
            const modifiedItem = document.createElement('div');
            modifiedItem.className = 'meta-item';
            modifiedItem.innerHTML = `<i class="fas fa-calendar-check"></i> <span class="meta-label">Last Modified:</span> <span class="meta-value">${this.escapeHtml(this.entity.frontmatter.last_modified)}</span>`;
            row1.appendChild(modifiedItem);
        }

        metaGrid.appendChild(row1);

        // Create row 2: file, normalized name, permalink
        const row2 = document.createElement('div');
        row2.className = 'entity-meta-grid';

        // Add file path
        const fileItem = document.createElement('div');
        fileItem.className = 'meta-item';
        fileItem.innerHTML = `<i class="fas fa-file-alt"></i> <span class="meta-label">File:</span> <span class="meta-value file-path">${this.escapeHtml(this.entity.file_path)}</span>`;
        row2.appendChild(fileItem);

        // Add normalized name if available
        if (this.entity.frontmatter && this.entity.frontmatter.normalized_name) {
            const normalizedNameItem = document.createElement('div');
            normalizedNameItem.className = 'meta-item';
            normalizedNameItem.innerHTML = `<i class="fas fa-font"></i> <span class="meta-label">Normalized Name:</span> <span class="meta-value">${this.escapeHtml(this.entity.frontmatter.normalized_name)}</span>`;
            row2.appendChild(normalizedNameItem);
        }

        // Add permalink if available
        if (this.entity.frontmatter && this.entity.frontmatter.permalink) {
            const permalinkItem = document.createElement('div');
            permalinkItem.className = 'meta-item';
            permalinkItem.innerHTML = `<i class="fas fa-link"></i> <span class="meta-label">Permalink:</span> <span class="meta-value">${this.escapeHtml(this.entity.frontmatter.permalink)}</span>`;
            row2.appendChild(permalinkItem);
        }

        metaGrid.appendChild(row2);

        // Row 3: tags by themselves
        if (tags.length > 0) {
            const tagsSection = document.createElement('div');
            tagsSection.className = 'meta-tags-section';
            tagsSection.innerHTML = '<h4>Tags</h4><div class="tags-container"></div>';

            const tagsContainer = tagsSection.querySelector('.tags-container');
            tags.forEach(tag => {
                const tagElement = document.createElement('span');
                tagElement.className = 'tag';
                tagElement.textContent = tag;
                tagsContainer.appendChild(tagElement);
            });

            metaGrid.appendChild(tagsSection);
        }

        // Add any remaining market cap or other fields if needed
        if (this.entity.market_cap) {
            const marketCapItem = document.createElement('div');
            marketCapItem.className = 'meta-item';
            marketCapItem.innerHTML = `<i class="fas fa-chart-line"></i> <span class="meta-label">Market Cap:</span> <span class="meta-value">${this.escapeHtml(this.entity.market_cap)}</span>`;

            // Append market cap to either row1 or create a new row if needed
            // For now, let's put it in row1 if there's space, or create a new row if necessary
            row1.appendChild(marketCapItem);
        }

        container.appendChild(metaGrid);
        metadataContainer.appendChild(container);
    }

    getEntityIcon(type) {
        const icons = {
            'company': 'building',
            'sector': 'industry',
            'default': 'folder'
        };
        return icons[type] || icons.default;
    }

    displayContent() {
        const contentContainer = document.getElementById('entity-content');
        
        if (this.entity.content) {
            const processedContent = this.processRichContent(this.entity.content);
            contentContainer.innerHTML = processedContent.html;
            
            // Generate table of contents
            const toc = this.generateTableOfContents(processedContent.headings);
            document.getElementById('toc-content').innerHTML = toc;
            
            // Initialize interactive elements
            this.initializeInteractiveElements();
            
            // Collect images for lightbox navigation
            this.collectImages();
        } else {
            contentContainer.innerHTML = '<div class="no-content"><i class="fas fa-file-alt"></i><p>No content available for this entity.</p></div>';
        }
    }

    processRichContent(content) {
        // Enhanced markdown processing (same as modal version)
        let processedHtml = marked.parse(content);

        // Sanitize the rendered HTML to prevent stored XSS from note content.
        // marked v4+ no longer sanitizes; DOMPurify strips event handlers,
        // <script>, javascript: URLs, etc. We allow the tags/attrs that our
        // post-processing below (lightbox onclick, data-*, loading) relies on.
        if (window.DOMPurify) {
            processedHtml = DOMPurify.sanitize(processedHtml, {
                ADD_TAGS: ['iframe'],  // rarely used but safe when sanitized
                ADD_ATTR: ['onclick', 'data-img-id', 'data-code-id', 'loading', 'target', 'rel']
            });
        }
        
        // Extract headings for TOC
        const headings = [];
        processedHtml = processedHtml.replace(/<h([1-6])[^>]*>(.*?)<\/h[1-6]>/gi, (match, level, text) => {
            const id = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
            headings.push({
                level: parseInt(level),
                text: text.replace(/<[^>]*>/g, ''),
                id: id
            });
            return `<h${level} id="${id}">${text}</h${level}>`;
        });
        
        // Process images for lightbox
        processedHtml = processedHtml.replace(/<img([^>]+)src="([^"]+)"([^>]*)>/gi, (match, before, src, after) => {
            const imgId = `img-${Math.random().toString(36).substr(2, 9)}`;
            return `<img${before}src="${src}"${after} class="rich-image" data-img-id="${imgId}" onclick="entityPage.openLightbox('${src}')" loading="lazy">`;
        });
        
        // Process code blocks with syntax highlighting
        processedHtml = processedHtml.replace(/<pre><code class="language-(\w+)">([\s\S]*?)<\/code><\/pre>/gi, (match, lang, code) => {
            const codeId = `code-${Math.random().toString(36).substr(2, 9)}`;
            const highlightedCode = this.highlightCode(code, lang);
            return `
                <div class="code-block">
                    <div class="code-header">
                        <span class="code-language">${lang}</span>
                        <button class="code-copy" onclick="entityPage.copyCode('${codeId}')" title="Copy code">
                            <i class="fas fa-copy"></i>
                        </button>
                    </div>
                    <pre><code id="${codeId}" class="language-${lang}">${highlightedCode}</code></pre>
                </div>
            `;
        });
        
        // Process inline code
        processedHtml = processedHtml.replace(/<code>([\s\S]*?)<\/code>/gi, '<code class="inline-code">$1</code>');
        
        // Process tables
        processedHtml = processedHtml.replace(/<table([^>]*)>([\s\S]*?)<\/table>/gi, (match, attributes, tableContent) => {
            return `
                <div class="table-wrapper">
                    <table${attributes}>${tableContent}</table>
                </div>
            `;
        });
        
        // Process blockquotes
        processedHtml = processedHtml.replace(/<blockquote>([\s\S]*?)<\/blockquote>/gi, '<blockquote class="rich-blockquote">$1</blockquote>');
        
        return {
            html: processedHtml,
            headings: headings
        };
    }

    generateTableOfContents(headings) {
        if (headings.length === 0) return '<p class="toc-empty">No headings found</p>';
        
        let toc = '<ul class="toc-list">';
        let currentLevel = 0;
        
        headings.forEach(heading => {
            if (heading.level > currentLevel) {
                toc += '<ul class="toc-nested">';
            } else if (heading.level < currentLevel) {
                toc += '</ul>'.repeat(currentLevel - heading.level);
            }
            
            toc += `
                <li class="toc-item toc-level-${heading.level}">
                    <a href="#${heading.id}" class="toc-link">
                        ${heading.text}
                    </a>
                </li>
            `;
            
            currentLevel = heading.level;
        });
        
        toc += '</ul>'.repeat(currentLevel);
        toc += '</ul>';
        
        return toc;
    }

    highlightCode(code, language) {
        try {
            if (window.hljs) {
                return window.hljs.highlight(code, { language }).value;
            }
        } catch (e) {
            console.warn('Syntax highlighting failed:', e);
        }
        return this.escapeHtml(code);
    }

    copyCode(codeId) {
        const codeElement = document.getElementById(codeId);
        if (codeElement) {
            const text = codeElement.textContent;
            navigator.clipboard.writeText(text).then(() => {
                this.showToast('Code copied to clipboard!', 'success');
            }).catch(err => {
                console.error('Failed to copy code:', err);
                this.showToast('Failed to copy code', 'error');
            });
        }
    }

    collectImages() {
        this.images = Array.from(document.querySelectorAll('.rich-image')).map(img => ({
            src: img.src,
            alt: img.alt || 'Image'
        }));
    }

    openLightbox(imageSrc) {
        const lightbox = document.getElementById('image-lightbox');
        const lightboxImage = document.getElementById('lightbox-image');
        const caption = document.querySelector('.lightbox-caption');
        
        // Find current image index
        this.currentImageIndex = this.images.findIndex(img => img.src === imageSrc);
        
        lightboxImage.src = imageSrc;
        caption.textContent = this.images[this.currentImageIndex]?.alt || 'Image';
        lightbox.style.display = 'flex';
        
        // Update navigation buttons
        this.updateLightboxNavigation();
        
        // Prevent body scroll
        document.body.style.overflow = 'hidden';
    }

    closeLightbox() {
        const lightbox = document.getElementById('image-lightbox');
        lightbox.style.display = 'none';
        document.body.style.overflow = '';
    }

    navigateLightbox(direction) {
        this.currentImageIndex += direction;
        
        if (this.currentImageIndex < 0) {
            this.currentImageIndex = this.images.length - 1;
        } else if (this.currentImageIndex >= this.images.length) {
            this.currentImageIndex = 0;
        }
        
        const currentImage = this.images[this.currentImageIndex];
        if (currentImage) {
            document.getElementById('lightbox-image').src = currentImage.src;
            document.querySelector('.lightbox-caption').textContent = currentImage.alt;
        }
        
        this.updateLightboxNavigation();
    }

    updateLightboxNavigation() {
        const prevBtn = document.getElementById('lightbox-prev');
        const nextBtn = document.getElementById('lightbox-next');
        
        prevBtn.style.display = this.images.length > 1 ? 'block' : 'none';
        nextBtn.style.display = this.images.length > 1 ? 'block' : 'none';
    }

    toggleTableOfContents() {
        const tocSidebar = document.getElementById('toc-sidebar');
        const mainContent = document.querySelector('.entity-article');
        const toggleBtn = document.getElementById('toggle-toc');
        
        if (tocSidebar.style.display === 'none' || !tocSidebar.style.display) {
            tocSidebar.style.display = 'block';
            mainContent.style.marginLeft = '300px';
            toggleBtn.classList.add('active');
        } else {
            this.hideTableOfContents();
        }
    }

    hideTableOfContents() {
        const tocSidebar = document.getElementById('toc-sidebar');
        const mainContent = document.querySelector('.entity-article');
        const toggleBtn = document.getElementById('toggle-toc');
        
        tocSidebar.style.display = 'none';
        mainContent.style.marginLeft = '0';
        toggleBtn.classList.remove('active');
    }

    toggleFullscreen() {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen();
        } else {
            document.exitFullscreen();
        }
    }

    updateFullscreenButton() {
        const btn = document.getElementById('toggle-fullscreen');
        const icon = btn.querySelector('i');
        const text = btn.querySelector('span');
        
        if (document.fullscreenElement) {
            icon.className = 'fas fa-compress';
            text.textContent = 'Exit Fullscreen';
        } else {
            icon.className = 'fas fa-expand';
            text.textContent = 'Fullscreen';
        }
    }

    exportContent() {
        const title = this.entity.name;
        const content = this.entity.content || '';
        const metadata = document.getElementById('entity-metadata').textContent;
        
        const fullContent = `
# ${title}

${metadata}

---

${content}
        `.trim();
        
        const blob = new Blob([fullContent], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${title.replace(/[^a-z0-9]/gi, '_')}.md`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        this.showToast('Content exported successfully!', 'success');
    }

    printContent() {
        window.print();
        this.showToast('Print dialog opened', 'success');
    }

    handleKeyboard(e) {
        // ESC to close lightbox
        if (e.key === 'Escape') {
            const lightbox = document.getElementById('image-lightbox');
            if (lightbox.style.display === 'flex') {
                this.closeLightbox();
            }
        }
        
        // Arrow keys for lightbox navigation
        if (document.getElementById('image-lightbox').style.display === 'flex') {
            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                this.navigateLightbox(-1);
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                this.navigateLightbox(1);
            }
        }
    }

    showLoading(show) {
        const loadingState = document.getElementById('loading-state');
        const errorState = document.getElementById('error-state');
        const mainContent = document.getElementById('main-content');
        
        if (show) {
            loadingState.style.display = 'flex';
            errorState.style.display = 'none';
            mainContent.style.display = 'none';
        } else {
            loadingState.style.display = 'none';
        }
    }

    showError(message) {
        const loadingState = document.getElementById('loading-state');
        const errorState = document.getElementById('error-state');
        const mainContent = document.getElementById('main-content');
        
        loadingState.style.display = 'none';
        errorState.style.display = 'flex';
        mainContent.style.display = 'none';
        
        const errorMessage = errorState.querySelector('p');
        if (errorMessage) {
            errorMessage.textContent = message;
        }
    }

    showToast(message, type = 'info') {
        // Create toast notification
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle'}"></i>
            <span>${message}</span>
        `;
        
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.remove();
        }, 3000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    initializeInteractiveElements() {
        // Initialize syntax highlighting
        if (window.Prism) {
            Prism.highlightAll();
        }
        
        // Add smooth scrolling for TOC links
        document.querySelectorAll('.toc-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const targetId = link.getAttribute('href').substring(1);
                const targetElement = document.getElementById(targetId);
                if (targetElement) {
                    targetElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        });
        
        // Add image loading error handling
        document.querySelectorAll('.rich-image').forEach(img => {
            img.addEventListener('error', () => {
                img.style.display = 'none';
                const placeholder = document.createElement('div');
                placeholder.className = 'image-placeholder';
                placeholder.innerHTML = '<i class="fas fa-image"></i><span>Image failed to load</span>';
                img.parentNode.insertBefore(placeholder, img);
            });
        });
    }
}

// Initialize the page
const entityPage = new EntityDetailPage();