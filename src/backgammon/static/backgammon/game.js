(function () {
    const app = document.getElementById('game-app');
    if (!app) {
        return;
    }

    const stateUrl = app.dataset.stateUrl;
    const rollUrl = app.dataset.rollUrl;
    const moveUrl = app.dataset.moveUrl;
    const undoUrl = app.dataset.undoUrl;
    const endTurnUrl = app.dataset.endTurnUrl;
    const prepareBearOffUrl = app.dataset.prepareBearOffUrl;
    const prepareVictoryUrl = app.dataset.prepareVictoryUrl;
    const debugGameTools = Boolean(prepareBearOffUrl && prepareVictoryUrl);
    const domovoyImages = [app.dataset.domovoyOne, app.dataset.domovoyTwo].filter(Boolean);
    const csrfToken = app.querySelector('[name=csrfmiddlewaretoken]').value;

    const boardEl = document.getElementById('board');
    const gameSide = document.querySelector('.game-side');
    const statusLine = document.getElementById('status-line');
    const errorLine = document.getElementById('error-line');
    const diceRow = document.getElementById('dice-row');
    const remainingRow = document.getElementById('remaining-row');
    const movePanel = document.getElementById('move-panel');
    const rollButton = document.getElementById('roll-button');
    const undoButton = document.getElementById('undo-button');
    const endTurnButton = document.getElementById('end-turn-button');
    const prepareBearOffButton = document.getElementById('prepare-bear-off-button');
    const prepareVictoryButton = document.getElementById('prepare-victory-button');
    const domovoyPop = document.getElementById('domovoy-pop');
    const domovoyImage = document.getElementById('domovoy-image');
    const whitePlayer = document.getElementById('white-player');
    const blackPlayer = document.getElementById('black-player');
    const whiteOff = document.getElementById('white-off');
    const blackOff = document.getElementById('black-off');

    let game = null;
    let selectedSource = null;
    let diceTimer = null;
    let diceAnimating = false;
    let diceAnimationStartedAt = 0;
    let lastDiceKey = '';
    let lastVictoryKey = '';
    const minDiceRollMs = 1100;

    function playerName(player) {
        return player ? player.username : 'ожидание';
    }

    function showError(message) {
        errorLine.textContent = message || '';
    }

    function victoryTypeName(type) {
        if (type === 'mars') {
            return 'Марс';
        }
        return '';
    }

    function victoryLabel(winner, type) {
        const typeName = victoryTypeName(type);
        return typeName ? `${winner} · ${typeName}` : winner;
    }

    async function requestJson(url, options) {
        const response = await fetch(url, {
            method: options.method || 'GET',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
            body: options.body ? JSON.stringify(options.body) : undefined,
        });
        const payload = await response.json();
        if (!payload.ok) {
            throw new Error(payload.error || 'Ошибка запроса.');
        }
        return payload.game;
    }

    function statusText() {
        if (!game) {
            return '';
        }
        if (game.status === 'waiting') {
            return 'Ожидание второго игрока';
        }
        if (game.status === 'finished') {
            const winner = playerName(game.winner);
            return `Победитель: ${victoryLabel(winner, game.victory_type)}`;
        }
        if (game.current_player) {
            if (isViewerTurn()) {
                return 'Ваш ход!';
            }
            return `🤔 жду хода ${game.current_player.username}`;
        }
        return 'Игра активна';
    }

    function diceHtml(values, animated) {
        if (!values || !values.length) {
            return '<span class="text-secondary small">не брошены</span>';
        }
        const remainingCounts = {};
        if (!animated && Array.isArray(game && game.remaining_moves)) {
            game.remaining_moves.forEach((value) => {
                remainingCounts[value] = (remainingCounts[value] || 0) + 1;
            });
        }
        const usedCounts = {};
        return values.map((value) => {
            let className = animated ? 'die rolling' : 'die';
            const used = usedCounts[value] || 0;
            usedCounts[value] = used + 1;
            if (!animated && remainingCounts[value] && used < remainingCounts[value]) {
                className += ' available';
            }
            return `<span class="${className}">${value}</span>`;
        }).join('');
    }

    function randomDice() {
        return [1 + Math.floor(Math.random() * 6), 1 + Math.floor(Math.random() * 6)];
    }

    function showDomovoy() {
        if (!domovoyPop || !domovoyImage || !domovoyImages.length) {
            return;
        }
        const image = domovoyImages[Math.floor(Math.random() * domovoyImages.length)];
        domovoyImage.src = image;
        domovoyPop.classList.remove('show');
        window.requestAnimationFrame(() => {
            domovoyPop.classList.add('show');
        });
        window.setTimeout(() => {
            domovoyPop.classList.remove('show');
        }, 3800);
    }

    function victoryBannerHtml() {
        if (!game || game.status !== 'finished') {
            return '';
        }
        const winner = playerName(game.winner);
        return `
            <div class="victory-banner">
                <div class="victory-title">Победа!</div>
                <div class="victory-subtitle">${victoryLabel(winner, game.victory_type)}</div>
            </div>
        `;
    }

    function maybeShowVictoryAnimation() {
        if (!game || game.status !== 'finished' || !game.winner) {
            return;
        }
        const victoryKey = `${game.id}:${game.winner.id}:${game.victory_type}`;
        if (lastVictoryKey === victoryKey) {
            return;
        }
        lastVictoryKey = victoryKey;
        showDomovoy();
    }

    function startDiceAnimation() {
        diceAnimating = true;
        diceAnimationStartedAt = Date.now();
        window.clearInterval(diceTimer);
        diceRow.innerHTML = diceHtml(randomDice(), true);
        diceTimer = window.setInterval(() => {
            diceRow.innerHTML = diceHtml(randomDice(), true);
        }, 85);
    }

    function stopDiceAnimation(values) {
        window.clearInterval(diceTimer);
        diceTimer = null;
        diceAnimating = false;
        diceRow.innerHTML = diceHtml(values, false);
    }

    function wait(ms) {
        return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    async function finishDiceAnimation(values) {
        const elapsed = Date.now() - diceAnimationStartedAt;
        if (elapsed < minDiceRollMs) {
            await wait(minDiceRollMs - elapsed);
        }
        stopDiceAnimation(values);
    }

    function flashDice(values) {
        if (!values || !values.length || diceAnimating) {
            return;
        }
        diceRow.innerHTML = diceHtml(values, true);
        window.setTimeout(() => {
            if (!diceAnimating && game) {
                diceRow.innerHTML = diceHtml(game.dice, false);
            }
        }, 520);
    }

    function moveMarkers() {
        if (Array.isArray(game.last_move_markers)) {
            return game.last_move_markers;
        }
        return game.last_move_marker ? [game.last_move_marker] : [];
    }

    function movedCheckerCount(index) {
        return moveMarkers().reduce((count, marker) => {
            if (marker.target === index) {
                return count + (marker.count || 1);
            }
            return count;
        }, 0);
    }

    function isLastMovePoint(index) {
        return movedCheckerCount(index) > 0;
    }

    function checkerHtml(stack, index) {
        if (!stack) {
            return '';
        }
        const visible = Math.min(stack.count, 5);
        const hidden = stack.count - visible;
        const highlighted = Math.min(movedCheckerCount(index), visible);
        const firstHighlighted = visible - highlighted;
        let html = '<div class="stack">';
        for (let i = 0; i < visible; i += 1) {
            const isMovedChecker = highlighted > 0 && i >= firstHighlighted;
            const markerClass = isMovedChecker ? ' last-move-checker' : '';
            html += `<span class="checker ${stack.color}${markerClass}">${i === visible - 1 && hidden > 0 ? '+' + hidden : ''}</span>`;
        }
        html += '</div>';
        return html;
    }

    function movesForSource(source) {
        return game.legal_moves.filter((move) => move.source === source);
    }

    function legalSources() {
        return new Set(game.legal_moves.map((move) => move.source));
    }

    function legalTargets() {
        if (selectedSource === null) {
            return new Set();
        }
        return new Set(movesForSource(selectedSource).map((move) => move.target).filter((target) => target !== null));
    }

    function displayRows() {
        if (game.viewer_color === 'black') {
            return {
                top: Array.from({ length: 12 }, (_, offset) => 11 - offset),
                bottom: Array.from({ length: 12 }, (_, offset) => 12 + offset),
            };
        }
        return {
            top: Array.from({ length: 12 }, (_, offset) => 23 - offset),
            bottom: Array.from({ length: 12 }, (_, offset) => offset),
        };
    }

    async function submitMove(source, distance) {
        try {
            showError('');
            game = await requestJson(moveUrl, {
                method: 'POST',
                body: { source, distance },
            });
            selectedSource = null;
            render();
        } catch (error) {
            showError(error.message);
        }
    }

    function moveToPoint(source, target) {
        return movesForSource(source).find((move) => move.target === target);
    }

    function isOwnStack(index) {
        const stack = game.board[index];
        return Boolean(stack && stack.color === game.viewer_color && stack.count > 0);
    }

    function renderPoint(index) {
        const sources = legalSources();
        const targets = legalTargets();
        const isSource = sources.has(index);
        const isTarget = targets.has(index);
        const point = document.createElement('button');
        point.type = 'button';
        point.className = 'point';
        point.dataset.index = index;
        point.setAttribute('aria-label', `Пункт ${index + 1}`);
        if (isOwnStack(index)) {
            point.classList.add('own-stack');
        }
        if (isLastMovePoint(index)) {
            point.classList.add('last-move-point');
        }
        if (isTarget) {
            point.classList.add('legal-target');
        } else if (isSource) {
            point.classList.add('legal-source');
        }
        if (selectedSource === index) {
            point.classList.add('selected');
        }
        point.innerHTML = `<span class="point-label">${index + 1}</span>${checkerHtml(game.board[index], index)}`;
        point.addEventListener('click', () => {
            if (selectedSource !== null) {
                const targetMove = moveToPoint(selectedSource, index);
                if (targetMove) {
                    submitMove(selectedSource, targetMove.distance);
                    return;
                }
            }
            if (isSource) {
                selectedSource = selectedSource === index ? null : index;
                render();
                return;
            }
            selectedSource = null;
            render();
        });
        return point;
    }

    function renderBoard() {
        boardEl.innerHTML = '';
        const top = document.createElement('div');
        top.className = 'board-row top';
        const bottom = document.createElement('div');
        bottom.className = 'board-row bottom';
        const rows = displayRows();

        rows.top.forEach((index) => {
            top.appendChild(renderPoint(index));
        });
        rows.bottom.forEach((index) => {
            bottom.appendChild(renderPoint(index));
        });

        boardEl.appendChild(top);
        boardEl.appendChild(bottom);
        boardEl.insertAdjacentHTML('beforeend', victoryBannerHtml());
    }

    function renderMoves() {
        movePanel.innerHTML = '';
        if (!game.legal_moves.length) {
            movePanel.innerHTML = '<div class="text-secondary small">Нет доступных ходов.</div>';
            return;
        }
        if (selectedSource === null) {
            movePanel.innerHTML = '<div class="text-secondary small">Выберите подсвеченную шашку.</div>';
            return;
        }

        const moves = movesForSource(selectedSource);
        const boardMoves = moves.filter((move) => move.action !== 'bear_off');
        const bearOffMoves = moves.filter((move) => move.action === 'bear_off');
        const hint = document.createElement('div');
        hint.className = 'text-secondary small mb-2';
        hint.textContent = boardMoves.length ? 'Кликните по зеленому пункту назначения.' : 'Можно вывести шашку с доски.';
        movePanel.appendChild(hint);

        bearOffMoves.forEach((move) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-outline-success btn-sm me-2 mb-2';
            button.textContent = `${move.distance} → выбросить`;
            button.addEventListener('click', () => submitMove(selectedSource, move.distance));
            movePanel.appendChild(button);
        });
    }

    function renderDice() {
        const diceKey = JSON.stringify(game.dice || []);
        const shouldFlashDice = lastDiceKey && diceKey !== lastDiceKey && game.dice.length > 0;
        lastDiceKey = diceKey;
        if (diceAnimating) {
            return;
        }
        diceRow.innerHTML = diceHtml(game.dice, false);
        if (shouldFlashDice) {
            flashDice(game.dice);
        }
    }

    function isViewerTurn() {
        return game.status === 'active' && (game.can_roll || game.can_end_turn || game.legal_moves.length > 0);
    }

    function renderTurnState() {
        gameSide.classList.remove('turn-mine', 'turn-waiting', 'turn-neutral');
        if (game.status !== 'active') {
            gameSide.classList.add('turn-neutral');
            return;
        }
        gameSide.classList.add(isViewerTurn() ? 'turn-mine' : 'turn-waiting');
    }

    function render() {
        if (!game) {
            return;
        }
        renderTurnState();
        whitePlayer.textContent = `Белые: ${playerName(game.white_player)}`;
        blackPlayer.textContent = `Черные: ${playerName(game.black_player)}`;
        statusLine.textContent = statusText();
        renderDice();
        remainingRow.textContent = game.remaining_moves.length ? `Осталось: ${game.remaining_moves.join(', ')}` : '';
        whiteOff.textContent = game.borne_off.white || 0;
        blackOff.textContent = game.borne_off.black || 0;
        rollButton.disabled = !game.can_roll || diceAnimating;
        undoButton.disabled = !game.can_undo || diceAnimating;
        endTurnButton.disabled = !game.can_end_turn || diceAnimating;
        if (prepareBearOffButton) {
            prepareBearOffButton.disabled = game.status !== 'active' || !game.viewer_color || diceAnimating;
        }
        if (prepareVictoryButton) {
            prepareVictoryButton.disabled = game.status !== 'active' || !game.viewer_color || diceAnimating;
        }
        if (selectedSource !== null && !legalSources().has(selectedSource)) {
            selectedSource = null;
        }
        renderBoard();
        renderMoves();
        maybeShowVictoryAnimation();
    }

    async function loadState() {
        try {
            game = await requestJson(stateUrl, { method: 'GET' });
            render();
        } catch (error) {
            showError(error.message);
        }
    }

    rollButton.addEventListener('click', async () => {
        try {
            showError('');
            startDiceAnimation();
            const nextGame = await requestJson(rollUrl, { method: 'POST' });
            await finishDiceAnimation(nextGame.dice);
            game = nextGame;
            lastDiceKey = JSON.stringify(game.dice || []);
            if (game.dice.length === 2 && game.dice[0] === game.dice[1]) {
                showDomovoy();
            }
            selectedSource = null;
            render();
        } catch (error) {
            await finishDiceAnimation(game ? game.dice : []);
            showError(error.message);
        }
    });

    endTurnButton.addEventListener('click', async () => {
        try {
            showError('');
            game = await requestJson(endTurnUrl, { method: 'POST' });
            selectedSource = null;
            render();
        } catch (error) {
            showError(error.message);
        }
    });

    undoButton.addEventListener('click', async () => {
        try {
            showError('');
            game = await requestJson(undoUrl, { method: 'POST' });
            selectedSource = null;
            render();
        } catch (error) {
            showError(error.message);
        }
    });

    if (debugGameTools && prepareBearOffButton) {
        prepareBearOffButton.addEventListener('click', async () => {
            try {
                showError('');
                game = await requestJson(prepareBearOffUrl, { method: 'POST' });
                lastDiceKey = JSON.stringify(game.dice || []);
                selectedSource = null;
                render();
            } catch (error) {
                showError(error.message);
            }
        });
    }

    if (debugGameTools && prepareVictoryButton) {
        prepareVictoryButton.addEventListener('click', async () => {
            try {
                showError('');
                game = await requestJson(prepareVictoryUrl, { method: 'POST' });
                lastDiceKey = JSON.stringify(game.dice || []);
                selectedSource = null;
                render();
            } catch (error) {
                showError(error.message);
            }
        });
    }

    loadState();
    window.setInterval(loadState, 2500);
}());
