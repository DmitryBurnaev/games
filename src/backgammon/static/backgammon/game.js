(function () {
    const app = document.getElementById('game-app');
    if (!app) {
        return;
    }

    const stateUrl = app.dataset.stateUrl;
    const stateWsUrl = app.dataset.stateWsUrl;
    const rollUrl = app.dataset.rollUrl;
    const moveUrl = app.dataset.moveUrl;
    const surrenderUrl = app.dataset.surrenderUrl;
    const notificationUrl = app.dataset.notificationUrl;
    const undoUrl = app.dataset.undoUrl;
    const endTurnUrl = app.dataset.endTurnUrl;
    const prepareBearOffUrl = app.dataset.prepareBearOffUrl;
    const prepareVictoryUrl = app.dataset.prepareVictoryUrl;
    const prepareFinalDoubleUrl = app.dataset.prepareFinalDoubleUrl;
    const prepareExtraHeadMoveUrl = app.dataset.prepareExtraHeadMoveUrl;
    const prepareBlockingEventUrl = app.dataset.prepareBlockingEventUrl;
    const debugGameTools = app.dataset.debugTools === '1';
    const moveAnimationsEnabled = app.dataset.animationsEnabled !== '0';
    const quickNotificationsEnabled = app.dataset.quickNotificationsEnabled === '1';
    const configuredPollIntervalMs = parseInt(app.dataset.pollIntervalMs || '1000', 10);
    const pollIntervalMs = Math.max(configuredPollIntervalMs || 1000, 250);
    const configuredNotificationDisplayMs = parseInt(app.dataset.notificationDisplayMs || '4500', 10);
    const fallbackNotificationDisplayMs = Math.max(configuredNotificationDisplayMs || 4500, 1000);
    const gameDebugId = debugGameTools ? app.dataset.gameId || null : null;
    const domovoyImages = [app.dataset.domovoyOne, app.dataset.domovoyTwo].filter(Boolean);
    const csrfToken = app.querySelector('[name=csrfmiddlewaretoken]').value;

    const boardEl = document.getElementById('board');
    const gameSide = document.querySelector('.game-side');
    const statusLine = document.getElementById('status-line');
    const gameDurationLine = document.getElementById('game-duration-line');
    const gameDuration = document.getElementById('game-duration');
    const errorLine = document.getElementById('error-line');
    const dicePanel = document.getElementById('dice-panel');
    const diceRow = document.getElementById('dice-row');
    const remainingRow = document.getElementById('remaining-row');
    const movePanel = document.getElementById('move-panel');
    const finishedStatsPanel = document.getElementById('finished-stats-panel');
    const controlPanel = document.getElementById('control-panel');
    const rollButton = document.getElementById('roll-button');
    const undoButton = document.getElementById('undo-button');
    const endTurnButton = document.getElementById('end-turn-button');
    const surrenderButton = document.getElementById('surrender-button');
    const quickNotificationToast = document.getElementById('quick-notification-toast');
    const quickNotificationActions = document.getElementById('quick-notification-actions');
    const quickNotificationButtons = Array.from(document.querySelectorAll('.quick-notification-button'));
    const prepareBearOffButton = document.getElementById('prepare-bear-off-button');
    const prepareVictoryButton = document.getElementById('prepare-victory-button');
    const prepareFinalDoubleButton = document.getElementById('prepare-final-double-button');
    const prepareExtraHeadMoveButton = document.getElementById('prepare-extra-head-move-button');
    const prepareBlockingEventButton = document.getElementById('prepare-blocking-event-button');
    const waitingForOpponentAlerts = Array.from(document.querySelectorAll('.alert'))
        .filter((element) => element.textContent.includes('Теперь нужен второй игрок'));
    const joinedGameAlerts = Array.from(document.querySelectorAll('.alert'))
        .filter((element) => element.textContent.includes('Вы присоединились к игре'));
    const domovoyPop = document.getElementById('domovoy-pop');
    const domovoyImage = document.getElementById('domovoy-image');
    const whitePlayer = document.getElementById('white-player');
    const blackPlayer = document.getElementById('black-player');
    const whiteOff = document.getElementById('white-off');
    const blackOff = document.getElementById('black-off');
    const whiteOffChecker = document.getElementById('white-off-checker');
    const blackOffChecker = document.getElementById('black-off-checker');

    let game = null;
    let selectedSource = null;
    let diceTimer = null;
    let diceAnimating = false;
    let diceAnimationStartedAt = 0;
    let diceAnimationFrame = 0;
    let domovoyTimer = null;
    let quickNotificationTimer = null;
    let joinedGameAlertTimer = null;
    let lastDiceKey = '';
    let lastVictoryKey = '';
    let nextMoveAnimationAt = 0;
    const animatedMoveKeys = new Set();
    const shownNotificationIds = new Set();
    const minDiceRollMs = 1100;
    const moveAnimationMs = {
        own: 300,
        opponent: 1300,
    };
    const moveAnimationStaggerMs = {
        own: 45,
        opponent: 240,
    };
    const heartbeatIntervalMs = 20000;
    const heartbeatTimeoutMs = 60000;
    const reconnectDelaysMs = [1000, 2000, 5000, 10000, 30000];
    let latestStateUpdatedAt = '';
    let latestRealtimeMessageAt = 0;
    let stateSocket = null;
    let pollTimer = null;
    let heartbeatTimer = null;
    let heartbeatWatchdogTimer = null;
    let reconnectTimer = null;
    let reconnectAttempt = 0;
    const movementPaths = {
        white: Array.from({ length: 24 }, (_, index) => index),
        black: Array.from({ length: 12 }, (_, offset) => 12 + offset).concat(
            Array.from({ length: 12 }, (_, offset) => offset),
        ),
    };
    const moveArcPx = {
        own: 46,
        opponent: 92,
    };

    function playerName(player) {
        return player ? player.display_name || player.username : 'ожидание';
    }

    function showError(message) {
        errorLine.textContent = message || '';
    }

    function notificationDisplayMs() {
        const stateDisplayMs = parseInt((game && game.notification_display_ms) || '', 10);
        return Math.max(stateDisplayMs || fallbackNotificationDisplayMs, 1000);
    }

    function showQuickNotification(notification) {
        if (!quickNotificationToast || !notification) {
            return;
        }
        window.clearTimeout(quickNotificationTimer);
        const sender = playerName(notification.sender);
        quickNotificationToast.textContent = sender
            ? `${sender}: ${notification.text}`
            : notification.text;
        quickNotificationToast.classList.add('show');
        quickNotificationTimer = window.setTimeout(() => {
            quickNotificationToast.classList.remove('show');
        }, notificationDisplayMs());
    }

    function renderQuickNotifications() {
        const notifications = Array.isArray(game.quick_notifications)
            ? game.quick_notifications
            : [];
        notifications.forEach((notification) => {
            if (!notification || shownNotificationIds.has(notification.id)) {
                return;
            }
            shownNotificationIds.add(notification.id);
            showQuickNotification(notification);
        });
        if (shownNotificationIds.size > 200) {
            shownNotificationIds.clear();
            notifications.forEach((notification) => {
                if (notification && notification.id) {
                    shownNotificationIds.add(notification.id);
                }
            });
        }
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

    function gameStartIso() {
        return game.started_at || game.created_at;
    }

    function moscowDateTimeLabel(value) {
        if (!value) {
            return '—';
        }
        return new Intl.DateTimeFormat('ru-RU', {
            timeZone: 'Europe/Moscow',
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        }).format(new Date(value)).replace(',', '');
    }

    function durationLabel(start, finish) {
        if (!start || !finish) {
            return '—';
        }
        const totalMinutes = Math.max(Math.floor((new Date(finish) - new Date(start)) / 60000), 0);
        const days = Math.floor(totalMinutes / 1440);
        const hours = Math.floor((totalMinutes % 1440) / 60);
        const minutes = totalMinutes % 60;
        const parts = [];
        if (days) {
            parts.push(`${days} д`);
        }
        if (hours) {
            const hourWord = hours === 1 ? 'час' : (hours >= 2 && hours <= 4 ? 'часа' : 'часов');
            parts.push(`${hours} ${hourWord}`);
        }
        if (minutes || !parts.length) {
            parts.push(`${minutes} мин`);
        }
        return parts.join(' ');
    }

    function renderCurrentGameDuration() {
        const isActive = game && game.status === 'active';
        gameDurationLine.classList.toggle('d-none', !isActive);
        gameDuration.textContent = isActive ? durationLabel(game.started_at, new Date()) : '—';
    }

    function diceValueLabel(value) {
        if (typeof value === 'number' || typeof value === 'string') {
            return String(value);
        }
        if (!value || typeof value !== 'object') {
            return '';
        }
        const namedValue = value.value || value.distance || value.die || value.roll;
        if (typeof namedValue === 'number' || typeof namedValue === 'string') {
            return String(namedValue);
        }
        return Object.values(value)
            .filter((item) => typeof item === 'number' || typeof item === 'string')
            .map((item) => String(item))
            .join('/');
    }

    function diceValueLabels(values) {
        return (values || []).map(diceValueLabel).filter(Boolean);
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

    function stateVersion(nextGame) {
        return nextGame && (nextGame.updated_at || nextGame.created_at || '');
    }

    function shouldIgnoreState(nextGame) {
        const nextVersion = stateVersion(nextGame);
        return Boolean(latestStateUpdatedAt && nextVersion && nextVersion < latestStateUpdatedAt);
    }

    function receiveGameState(nextGame, defaultAnimationSpeed, skipUnchangedRender) {
        if (shouldIgnoreState(nextGame)) {
            return;
        }
        latestStateUpdatedAt = stateVersion(nextGame) || latestStateUpdatedAt;
        applyGameState(nextGame, defaultAnimationSpeed, skipUnchangedRender);
    }

    function stateWebSocketUrl() {
        if (!stateWsUrl || !('WebSocket' in window)) {
            return null;
        }
        const url = new URL(stateWsUrl, window.location.href);
        url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return url.toString();
    }

    function startPollingFallback() {
        if (pollTimer) {
            return;
        }
        pollTimer = window.setInterval(loadState, pollIntervalMs);
    }

    function stopPollingFallback() {
        if (!pollTimer) {
            return;
        }
        window.clearInterval(pollTimer);
        pollTimer = null;
    }

    function clearRealtimeTimers() {
        if (heartbeatTimer) {
            window.clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
        if (heartbeatWatchdogTimer) {
            window.clearInterval(heartbeatWatchdogTimer);
            heartbeatWatchdogTimer = null;
        }
    }

    function markRealtimeAlive() {
        latestRealtimeMessageAt = Date.now();
        stopPollingFallback();
    }

    function scheduleRealtimeReconnect() {
        if (reconnectTimer) {
            return;
        }
        const delay = reconnectDelaysMs[Math.min(reconnectAttempt, reconnectDelaysMs.length - 1)];
        reconnectAttempt += 1;
        reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connectStateSocket();
        }, delay);
    }

    function startRealtimeHeartbeat(socket) {
        clearRealtimeTimers();
        heartbeatTimer = window.setInterval(() => {
            if (socket.readyState !== WebSocket.OPEN) {
                startPollingFallback();
                return;
            }
            socket.send(JSON.stringify({ type: 'ping' }));
        }, heartbeatIntervalMs);
        heartbeatWatchdogTimer = window.setInterval(() => {
            if (Date.now() - latestRealtimeMessageAt <= heartbeatTimeoutMs) {
                return;
            }
            startPollingFallback();
            if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
                socket.close();
            }
        }, heartbeatIntervalMs);
    }

    function connectStateSocket() {
        const url = stateWebSocketUrl();
        if (!url) {
            startPollingFallback();
            return;
        }
        if (
            stateSocket
            && (stateSocket.readyState === WebSocket.OPEN
                || stateSocket.readyState === WebSocket.CONNECTING)
        ) {
            return;
        }
        const socket = new WebSocket(url);
        stateSocket = socket;
        const connectFallbackTimer = window.setTimeout(() => {
            if (socket.readyState === WebSocket.CONNECTING) {
                startPollingFallback();
            }
        }, 5000);

        socket.addEventListener('open', () => {
            window.clearTimeout(connectFallbackTimer);
            reconnectAttempt = 0;
            latestRealtimeMessageAt = Date.now();
            startRealtimeHeartbeat(socket);
            socket.send(JSON.stringify({ type: 'ping' }));
        });

        socket.addEventListener('message', (event) => {
            let message = null;
            try {
                message = JSON.parse(event.data);
            } catch (error) {
                return;
            }
            if (message.type === 'pong') {
                markRealtimeAlive();
                return;
            }
            if (message.type === 'game_state' && message.game) {
                markRealtimeAlive();
                receiveGameState(message.game, 'auto', true);
            }
        });

        socket.addEventListener('close', () => {
            window.clearTimeout(connectFallbackTimer);
            if (stateSocket === socket) {
                stateSocket = null;
            }
            clearRealtimeTimers();
            startPollingFallback();
            scheduleRealtimeReconnect();
        });

        socket.addEventListener('error', () => {
            window.clearTimeout(connectFallbackTimer);
            startPollingFallback();
            socket.close();
        });
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
            return `🤔 жду хода ${playerName(game.current_player)}`;
        }
        return 'Игра активна';
    }

    function diceHtml(values, animated) {
        const labels = diceValueLabels(values);
        if (!labels.length) {
            return '<span class="text-secondary small">не брошены</span>';
        }
        const remainingCounts = {};
        if (!animated && Array.isArray(game && game.remaining_moves)) {
            diceValueLabels(game.remaining_moves).forEach((value) => {
                remainingCounts[value] = (remainingCounts[value] || 0) + 1;
            });
        }
        const usedCounts = {};
        return labels.map((value) => {
            let className = animated ? 'die rolling' : 'die';
            const used = usedCounts[value] || 0;
            usedCounts[value] = used + 1;
            if (!animated && remainingCounts[value] && used < remainingCounts[value]) {
                className += ' available';
            }
            return `<span class="${className}">${value}</span>`;
        }).join('');
    }

    function rollingDiceFrame() {
        const left = 1 + (diceAnimationFrame % 6);
        const right = 1 + ((diceAnimationFrame * 5 + 2) % 6);
        diceAnimationFrame += 1;
        return [left, right];
    }

    function showDomovoy(persist) {
        if (!domovoyPop || !domovoyImage || !domovoyImages.length) {
            return;
        }
        window.clearTimeout(domovoyTimer);
        const image = domovoyImages[Math.floor(Math.random() * domovoyImages.length)];
        domovoyImage.src = image;
        domovoyPop.classList.remove('show');
        window.requestAnimationFrame(() => {
            domovoyPop.classList.add('show');
        });
        if (persist) {
            return;
        }
        domovoyTimer = window.setTimeout(() => {
            domovoyPop.classList.remove('show');
        }, 7600);
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
        showDomovoy(true);
    }

    function startDiceAnimation() {
        diceAnimating = true;
        diceAnimationStartedAt = Date.now();
        diceAnimationFrame = 0;
        window.clearInterval(diceTimer);
        diceRow.innerHTML = diceHtml(rollingDiceFrame(), true);
        diceTimer = window.setInterval(() => {
            diceRow.innerHTML = diceHtml(rollingDiceFrame(), true);
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

    function stackCount(board, index, color) {
        const stack = board && board[index];
        if (!stack || stack.color !== color) {
            return 0;
        }
        return stack.count || 0;
    }

    function rectPayload(element) {
        if (!element) {
            return null;
        }
        const rect = element.getBoundingClientRect();
        return {
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
        };
    }

    function columnLabel(index) {
        return index === null || index === undefined ? 'off' : index + 1;
    }

    function animationTransitionPayload(transition) {
        return {
            id: transition.id ?? null,
            color: transition.color,
            source: transition.source,
            sourceColumn: columnLabel(transition.source),
            target: transition.target,
            targetColumn: columnLabel(transition.target),
        };
    }

    function pointDebugPayload(index) {
        if (index === null || index === undefined) {
            return null;
        }
        const point = boardEl.querySelector(`.point[data-index="${index}"]`);
        const checkers = point
            ? Array.from(point.querySelectorAll('.checker')).map((checker, checkerIndex) => ({
                order: checkerIndex,
                rect: rectPayload(checker),
                className: checker.className,
                text: checker.textContent,
            }))
            : [];
        return {
            index,
            column: columnLabel(index),
            row: game ? displayRowForPoint(index) : null,
            rect: rectPayload(point),
            checkerCount: checkers.length,
            checkers,
        };
    }

    function snapshotPointPayload(snapshot, index) {
        if (index === null || index === undefined || !snapshot || !snapshot.points[index]) {
            return null;
        }
        const point = snapshot.points[index];
        return {
            index,
            column: columnLabel(index),
            rect: point.rect,
            remainingSnapshotCheckers: point.checkers.length,
        };
    }

    function animationContextPayload() {
        return {
            gameId: gameDebugId,
            viewerColor: game ? game.viewer_color : null,
            currentPlayer: game ? game.current_player : null,
            status: game ? game.status : null,
            board: rectPayload(boardEl),
            viewport: {
                width: window.innerWidth,
                height: window.innerHeight,
            },
        };
    }

    function logAnimation(action, payload) {
        if (!debugGameTools) {
            return;
        }
        console.log('[backgammon:animation]', action, {
            at: new Date().toISOString(),
            ...animationContextPayload(),
            ...payload,
        });
    }

    function pointFallbackRect(index, color) {
        const point = boardEl.querySelector(`.point[data-index="${index}"]`);
        if (!point) {
            return null;
        }
        const pointRect = point.getBoundingClientRect();
        const size = Math.min(pointRect.width * 0.9, 36);
        const top = color === 'black' ? pointRect.top + 10 : pointRect.bottom - size - 10;
        return {
            left: pointRect.left + (pointRect.width - size) / 2,
            top: Math.max(pointRect.top + 8, Math.min(top, pointRect.bottom - size - 8)),
            width: size,
            height: size,
        };
    }

    function captureBoardSnapshot() {
        const snapshot = {
            points: {},
            off: {
                white: rectPayload(whiteOffChecker),
                black: rectPayload(blackOffChecker),
            },
        };
        boardEl.querySelectorAll('.point').forEach((point) => {
            const index = Number(point.dataset.index);
            const checkers = Array.from(point.querySelectorAll('.checker')).map((checker) => ({
                rect: rectPayload(checker),
                className: checker.className,
                text: checker.textContent,
            }));
            snapshot.points[index] = {
                rect: rectPayload(point),
                checkers,
            };
        });
        return snapshot;
    }

    function moveAnimationKey(move) {
        if (move.id !== undefined && move.id !== null) {
            return `move:${move.id}`;
        }
        return [
            'move',
            move.color,
            move.source,
            move.target,
            move.distance,
            move.action,
        ].join(':');
    }

    function visibleMoveSteps(nextGame) {
        return Array.isArray(nextGame && nextGame.last_move_steps)
            ? nextGame.last_move_steps
            : [];
    }

    function seedAnimatedMoveKeys(nextGame) {
        visibleMoveSteps(nextGame).forEach((move) => {
            animatedMoveKeys.add(moveAnimationKey(move));
        });
    }

    function newMoveStepTransitions(nextGame) {
        return visibleMoveSteps(nextGame)
            .filter((move) => {
                const key = moveAnimationKey(move);
                if (animatedMoveKeys.has(key)) {
                    return false;
                }
                animatedMoveKeys.add(key);
                return true;
            })
            .map((move) => ({
                id: move.id,
                color: move.color,
                source: move.source,
                target: move.target,
            }));
    }

    function pointPathPosition(color, point) {
        return movementPaths[color].indexOf(point);
    }

    function pathDistance(color, source, target) {
        const sourcePosition = pointPathPosition(color, source);
        const targetPosition = pointPathPosition(color, target);
        if (sourcePosition < 0 || targetPosition < 0) {
            return Number.POSITIVE_INFINITY;
        }
        return (targetPosition - sourcePosition + 24) % 24 || 24;
    }

    function expandChange(point, count) {
        return Array.from({ length: count }, () => ({ point }));
    }

    function inferCheckerTransitions(previousGame, nextGame) {
        if (!previousGame || !nextGame) {
            return [];
        }
        const transitions = [];
        ['white', 'black'].forEach((color) => {
            let sources = [];
            let targets = [];
            for (let index = 0; index < 24; index += 1) {
                const delta = stackCount(nextGame.board, index, color) - stackCount(previousGame.board, index, color);
                if (delta < 0) {
                    sources = sources.concat(expandChange(index, Math.abs(delta)));
                } else if (delta > 0) {
                    targets = targets.concat(expandChange(index, delta));
                }
            }

            const previousOff = previousGame.borne_off[color] || 0;
            const nextOff = nextGame.borne_off[color] || 0;
            if (nextOff > previousOff) {
                targets = targets.concat(expandChange(null, nextOff - previousOff));
            } else if (previousOff > nextOff) {
                sources = sources.concat(expandChange(null, previousOff - nextOff));
            }

            sources.forEach((source) => {
                if (!targets.length) {
                    return;
                }
                let bestIndex = 0;
                let bestDistance = Number.POSITIVE_INFINITY;
                targets.forEach((target, targetIndex) => {
                    const distance = source.point === null || target.point === null
                        ? 24
                        : pathDistance(color, source.point, target.point);
                    if (distance < bestDistance) {
                        bestDistance = distance;
                        bestIndex = targetIndex;
                    }
                });
                const [target] = targets.splice(bestIndex, 1);
                transitions.push({
                    color,
                    source: source.point,
                    target: target.point,
                });
            });
        });
        return transitions;
    }

    function takeStartChecker(snapshot, transition) {
        if (transition.source === null) {
            return {
                rect: snapshot.off[transition.color],
                className: `checker ${transition.color}`,
                text: '',
            };
        }
        const point = snapshot.points[transition.source];
        const checker = point && point.checkers.shift();
        if (checker) {
            return checker;
        }
        return {
            rect: pointFallbackRect(transition.source, transition.color),
            className: `checker ${transition.color}`,
            text: '',
        };
    }

    function movingCheckerClass(className, color) {
        return (className || `checker ${color}`)
            .replace(/\blast-move-checker\b/g, '')
            .replace(/\bmove-arrival-hidden\b/g, '')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function takeArrivalChecker(transition) {
        if (transition.target === null) {
            return {
                rect: rectPayload(
                    transition.color === 'white' ? whiteOffChecker : blackOffChecker,
                ),
                element: null,
            };
        }
        const point = boardEl.querySelector(`.point[data-index="${transition.target}"]`);
        const checkers = point
            ? Array.from(point.querySelectorAll(`.checker.${transition.color}:not(.move-arrival-hidden)`))
            : [];
        const checker = checkers[0];
        if (checker) {
            checker.classList.add('move-arrival-hidden');
            return {
                rect: rectPayload(checker),
                element: checker,
            };
        }
        return {
            rect: pointFallbackRect(transition.target, transition.color),
            element: null,
        };
    }

    function movementSpeedFor(transition, defaultSpeed) {
        if (defaultSpeed && defaultSpeed !== 'auto') {
            return defaultSpeed;
        }
        return transition.color === game.viewer_color ? 'own' : 'opponent';
    }

    function moveAnimationTiming(transition, defaultSpeed) {
        const speed = movementSpeedFor(transition, defaultSpeed);
        return {
            speed,
            duration: moveAnimationMs[speed] || moveAnimationMs.opponent,
            stagger: moveAnimationStaggerMs[speed] || moveAnimationStaggerMs.opponent,
        };
    }

    function animateCheckerTransition(transition, snapshot, delay, defaultSpeed) {
        const scheduledAt = debugGameTools ? Date.now() : 0;
        const timing = moveAnimationTiming(transition, defaultSpeed);
        const transitionPayload = debugGameTools ? animationTransitionPayload(transition) : null;
        if (debugGameTools) {
            logAnimation('schedule-transition', {
                transition: transitionPayload,
                delay,
                timing,
                queue: {
                    scheduledAt,
                    nextMoveAnimationAt,
                },
            });
        }
        window.setTimeout(() => {
            const startedAt = Date.now();
            const start = takeStartChecker(snapshot, transition);
            const arrival = takeArrivalChecker(transition);
            if (!start.rect || !arrival.rect) {
                if (arrival.element) {
                    arrival.element.classList.remove('move-arrival-hidden');
                }
                if (debugGameTools) {
                    logAnimation('skip-transition', {
                        transition: transitionPayload,
                        reason: !start.rect ? 'missing-start-rect' : 'missing-arrival-rect',
                        delay,
                        timing,
                        sourcePoint: pointDebugPayload(transition.source),
                        targetPoint: pointDebugPayload(transition.target),
                        sourceSnapshotPoint: snapshotPointPayload(snapshot, transition.source),
                        start: {
                            rect: start.rect,
                            className: start.className,
                            text: start.text,
                        },
                        arrival: {
                            rect: arrival.rect,
                            hasElement: Boolean(arrival.element),
                        },
                    });
                }
                return;
            }

            const wrapper = document.createElement('span');
            const checker = document.createElement('span');
            const isBearingOff = transition.target === null;
            checker.className = `${movingCheckerClass(start.className, transition.color)} moving-checker`;
            checker.textContent = start.text || '';
            wrapper.className = `moving-checker-wrap${isBearingOff ? ' bearing-off-checker-wrap' : ''}`;
            wrapper.style.left = `${start.rect.left}px`;
            wrapper.style.top = `${start.rect.top}px`;
            wrapper.style.width = `${start.rect.width}px`;
            wrapper.style.height = `${start.rect.height}px`;
            wrapper.style.transitionDuration = `${timing.duration}ms`;
            const arcHeight = isBearingOff
                ? -(moveArcPx[timing.speed] || moveArcPx.opponent)
                : (timing.speed === 'opponent' ? 1 : -1) * (moveArcPx[timing.speed] || moveArcPx.opponent);
            checker.style.setProperty('--move-arc-height', `${arcHeight}px`);
            checker.style.width = `${start.rect.width}px`;
            checker.style.height = `${start.rect.height}px`;
            checker.style.animationDuration = `${timing.duration}ms`;
            wrapper.appendChild(checker);
            document.body.appendChild(wrapper);

            const dx = arrival.rect.left - start.rect.left;
            const dy = arrival.rect.top - start.rect.top;
            if (debugGameTools) {
                logAnimation('start-transition', {
                    transition: transitionPayload,
                    waitedMs: startedAt - scheduledAt,
                    delay,
                    timing,
                    coordinates: {
                        start: start.rect,
                        arrival: arrival.rect,
                        dx,
                        dy,
                        arcHeight,
                    },
                    sourcePoint: pointDebugPayload(transition.source),
                    targetPoint: pointDebugPayload(transition.target),
                    sourceSnapshotPoint: snapshotPointPayload(snapshot, transition.source),
                });
            }
            const cleanup = () => {
                wrapper.remove();
                if (arrival.element) {
                    arrival.element.classList.remove('move-arrival-hidden');
                }
                if (debugGameTools) {
                    logAnimation('finish-transition', {
                        transition: transitionPayload,
                        elapsedMs: Date.now() - startedAt,
                        timing,
                    });
                }
            };

            window.requestAnimationFrame(() => {
                wrapper.style.transform = `translate(${dx}px, ${dy}px)`;
            });
            window.setTimeout(cleanup, timing.duration + 90);
        }, delay);
    }

    function animateCheckerTransitions(transitions, snapshot, defaultSpeed, source) {
        if (!transitions.length) {
            return;
        }
        const now = Date.now();
        let delay = Math.max(0, nextMoveAnimationAt - now);
        let queueEndDelay = delay;
        if (debugGameTools) {
            logAnimation('schedule-batch', {
                source,
                defaultSpeed,
                count: transitions.length,
                transitions: transitions.map(animationTransitionPayload),
                queue: {
                    now,
                    nextMoveAnimationAt,
                    initialDelay: delay,
                },
            });
        }

        transitions.forEach((transition) => {
            const timing = moveAnimationTiming(transition, defaultSpeed);
            animateCheckerTransition(transition, snapshot, delay, defaultSpeed);
            queueEndDelay = Math.max(queueEndDelay, delay + timing.duration + 90);
            delay += timing.speed === 'opponent'
                ? timing.duration + timing.stagger
                : timing.stagger;
        });

        nextMoveAnimationAt = now + queueEndDelay;
        if (debugGameTools) {
            logAnimation('batch-queued', {
                source,
                count: transitions.length,
                queue: {
                    now,
                    queueEndDelay,
                    nextMoveAnimationAt,
                },
            });
        }
    }

    function applyGameState(nextGame, defaultAnimationSpeed, skipUnchangedRender) {
        const previousGame = game;
        if (
            skipUnchangedRender
            && previousGame
            && gameStateKey(previousGame) === gameStateKey(nextGame)
        ) {
            game = nextGame;
            return;
        }
        if (!moveAnimationsEnabled) {
            game = nextGame;
            render();
            seedAnimatedMoveKeys(nextGame);
            return;
        }
        const snapshot = previousGame ? captureBoardSnapshot() : null;
        const transitions = previousGame ? newMoveStepTransitions(nextGame) : [];
        game = nextGame;
        render();
        if (!snapshot) {
            seedAnimatedMoveKeys(nextGame);
            return;
        }
        const fallbackTransitions = transitions.length
            ? []
            : inferCheckerTransitions(previousGame, nextGame);
        const animationSource = transitions.length ? 'last_move_steps' : 'board_diff';
        animateCheckerTransitions(
            transitions.length ? transitions : fallbackTransitions,
            snapshot,
            defaultAnimationSpeed || 'auto',
            animationSource,
        );
        if (animatedMoveKeys.size > 500) {
            animatedMoveKeys.clear();
            seedAnimatedMoveKeys(nextGame);
        }
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

    function gameStateKey(gameState) {
        return JSON.stringify(gameState);
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

    function displayRowForPoint(index) {
        const rows = displayRows();
        return rows.top.includes(index) ? 'top' : 'bottom';
    }

    function checkerHtml(stack, index) {
        if (!stack) {
            return '';
        }
        const visible = Math.min(stack.count, 5);
        const hidden = stack.count - visible;
        const highlighted = Math.min(movedCheckerCount(index), visible);
        const highlightFromStart = displayRowForPoint(index) === 'bottom';
        const firstHighlighted = visible - highlighted;
        let html = '<div class="stack">';
        for (let i = 0; i < visible; i += 1) {
            const isMovedChecker = highlighted > 0 && (
                highlightFromStart ? i < highlighted : i >= firstHighlighted
            );
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
            const nextGame = await requestJson(moveUrl, {
                method: 'POST',
                body: { source, distance },
            });
            selectedSource = null;
            receiveGameState(nextGame, 'own');
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
        const pointLabel = debugGameTools ? `<span class="point-label">${index + 1}</span>` : '';
        point.innerHTML = `${pointLabel}${checkerHtml(game.board[index], index)}`;
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
        movePanel.hidden = game.status === 'finished';
        if (movePanel.hidden) {
            return;
        }
        if (game.blocking_event && !game.legal_moves.length) {
            movePanel.innerHTML = '<div class="text-danger small">Нельзя завершить ход: разбейте блок из 6 пунктов без шашки соперника впереди.</div>';
            return;
        }
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

    function renderFinishedStats() {
        finishedStatsPanel.classList.toggle('show', game.status === 'finished');
        finishedStatsPanel.innerHTML = '';
        if (game.status !== 'finished') {
            return;
        }
        const start = gameStartIso();
        const finish = game.finished_at;
        const whiteDiceStats = game.dice_statistics ? game.dice_statistics.white || {} : {};
        const blackDiceStats = game.dice_statistics ? game.dice_statistics.black || {} : {};
        const skippedTurns = game.skipped_turns || {};
        const skippedPoints = game.skipped_points || {};
        const doubleStatisticsLabel = (statistics) => `${statistics.double_rolls || 0} / <span class="text-secondary">исп. ${statistics.double_moves_used || 0} из ${statistics.double_moves_available || 0}</span>`;
        finishedStatsPanel.innerHTML = `
            <div class="finished-stats-title">Итоги</div>
            <dl class="small">
                <dt>⌛ Время</dt>
                <dd>${durationLabel(start, finish)}</dd>
                <dt>🕰️ Начало</dt>
                <dd>${moscowDateTimeLabel(start)}</dd>
                <dt>🏁 Финиш</dt>
                <dd>${moscowDateTimeLabel(finish)}</dd>
                <dt>🎲 Сумма очков</dt>
                <dd>${playerName(game.white_player)}: ${whiteDiceStats.total_points || 0} · ${playerName(game.black_player)}: ${blackDiceStats.total_points || 0}</dd>
            </dl>
            <div class="finished-stats-title">Статистика</div>
            <dl class="small">
                <dt>Дубли (${playerName(game.white_player)})</dt>
                <dd>${doubleStatisticsLabel(whiteDiceStats)}</dd>
                <dt>Дубли (${playerName(game.black_player)})</dt>
                <dd>${doubleStatisticsLabel(blackDiceStats)}</dd>
                <dt>Пропуск (${playerName(game.white_player)})</dt>
                <dd>${skippedTurns.white || 0} / <span class="text-secondary">очков: ${skippedPoints.white || 0}</span></dd>
                <dt>Пропуск (${playerName(game.black_player)})</dt>
                <dd>${skippedTurns.black || 0} / <span class="text-secondary">очков: ${skippedPoints.black || 0}</span></dd>
            </dl>
        `;
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

    function renderPageMessages() {
        waitingForOpponentAlerts.forEach((element) => {
            element.classList.toggle('d-none', game.status !== 'waiting');
        });
        if (game.status !== 'active' || joinedGameAlertTimer || !joinedGameAlerts.length) {
            return;
        }
        joinedGameAlertTimer = window.setTimeout(() => {
            joinedGameAlerts.forEach((element) => {
                element.classList.add('d-none');
            });
        }, 2200);
    }

    function render() {
        if (!game) {
            return;
        }
        renderTurnState();
        renderPageMessages();
        whitePlayer.textContent = `Белые: ${playerName(game.white_player)}`;
        blackPlayer.textContent = `Черные: ${playerName(game.black_player)}`;
        statusLine.textContent = statusText();
        renderCurrentGameDuration();
        dicePanel.classList.toggle('d-none', game.status === 'finished');
        renderDice();
        const remainingLabels = diceValueLabels(game.remaining_moves);
        remainingRow.textContent = remainingLabels.length ? `Осталось: ${remainingLabels.join(', ')}` : '';
        whiteOff.textContent = game.borne_off.white || 0;
        blackOff.textContent = game.borne_off.black || 0;
        controlPanel.classList.toggle('d-none', game.status !== 'active');
        rollButton.disabled = !game.can_roll || diceAnimating;
        undoButton.disabled = !game.can_undo || diceAnimating;
        endTurnButton.disabled = !game.can_end_turn || diceAnimating;
        surrenderButton.disabled = !game.can_surrender || diceAnimating;
        surrenderButton.classList.toggle('d-none', game.status !== 'active');
        if (quickNotificationActions) {
            quickNotificationActions.classList.toggle(
                'd-none',
                !quickNotificationsEnabled || !game.can_send_quick_notifications,
            );
        }
        quickNotificationButtons.forEach((button) => {
            button.disabled = !quickNotificationsEnabled || !game.can_send_quick_notifications;
        });
        if (prepareBearOffButton) {
            prepareBearOffButton.disabled = game.status !== 'active' || !game.viewer_color || diceAnimating;
        }
        if (prepareVictoryButton) {
            prepareVictoryButton.disabled = game.status !== 'active' || !game.viewer_color || diceAnimating;
        }
        if (prepareFinalDoubleButton) {
            prepareFinalDoubleButton.disabled = game.status !== 'active' || !game.viewer_color || diceAnimating;
        }
        if (prepareExtraHeadMoveButton) {
            prepareExtraHeadMoveButton.disabled = game.status !== 'active' || !game.viewer_color || diceAnimating;
        }
        if (prepareBlockingEventButton) {
            prepareBlockingEventButton.disabled = game.status !== 'active' || !game.viewer_color || diceAnimating;
        }
        if (selectedSource !== null && !legalSources().has(selectedSource)) {
            selectedSource = null;
        }
        renderBoard();
        renderMoves();
        renderFinishedStats();
        renderQuickNotifications();
        maybeShowVictoryAnimation();
    }

    async function loadState() {
        try {
            const nextGame = await requestJson(stateUrl, { method: 'GET' });
            receiveGameState(nextGame, 'auto', true);
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
            selectedSource = null;
            receiveGameState(nextGame, 'auto');
            lastDiceKey = JSON.stringify(game.dice || []);
            if (game.dice.length === 2 && game.dice[0] === game.dice[1]) {
                showDomovoy();
            }
        } catch (error) {
            await finishDiceAnimation(game ? game.dice : []);
            showError(error.message);
        }
    });

    endTurnButton.addEventListener('click', async () => {
        try {
            showError('');
            const nextGame = await requestJson(endTurnUrl, { method: 'POST' });
            selectedSource = null;
            receiveGameState(nextGame, 'auto');
        } catch (error) {
            showError(error.message);
        }
    });

    undoButton.addEventListener('click', async () => {
        try {
            showError('');
            const nextGame = await requestJson(undoUrl, { method: 'POST' });
            selectedSource = null;
            receiveGameState(nextGame, 'own');
        } catch (error) {
            showError(error.message);
        }
    });

    surrenderButton.addEventListener('click', async () => {
        try {
            showError('');
            const confirmed = window.confirm('Сдаться? Победа будет засчитана сопернику.');
            if (!confirmed) {
                return;
            }
            let victoryType = 'oin';
            if (game.surrender_mars_available) {
                const markMars = window.confirm('На доске есть блок соперника из 6 пунктов вне дома. Засчитать поражение как марс?');
                if (markMars) {
                    victoryType = 'mars';
                }
            }
            const nextGame = await requestJson(surrenderUrl, {
                method: 'POST',
                body: { victory_type: victoryType },
            });
            selectedSource = null;
            receiveGameState(nextGame, 'auto');
        } catch (error) {
            showError(error.message);
        }
    });

    quickNotificationButtons.forEach((button) => {
        button.addEventListener('click', async () => {
            try {
                showError('');
                button.disabled = true;
                const nextGame = await requestJson(notificationUrl, {
                    method: 'POST',
                    body: { text: button.dataset.notificationText },
                });
                receiveGameState(nextGame, 'auto');
            } catch (error) {
                showError(error.message);
            } finally {
                if (game && quickNotificationsEnabled && game.can_send_quick_notifications) {
                    button.disabled = false;
                }
            }
        });
    });

    if (debugGameTools && prepareBearOffButton) {
        prepareBearOffButton.addEventListener('click', async () => {
            try {
                showError('');
                const nextGame = await requestJson(prepareBearOffUrl, { method: 'POST' });
                selectedSource = null;
                receiveGameState(nextGame, 'own');
                lastDiceKey = JSON.stringify(game.dice || []);
            } catch (error) {
                showError(error.message);
            }
        });
    }

    if (debugGameTools && prepareVictoryButton) {
        prepareVictoryButton.addEventListener('click', async () => {
            try {
                showError('');
                const nextGame = await requestJson(prepareVictoryUrl, { method: 'POST' });
                selectedSource = null;
                receiveGameState(nextGame, 'own');
                lastDiceKey = JSON.stringify(game.dice || []);
            } catch (error) {
                showError(error.message);
            }
        });
    }

    if (debugGameTools && prepareFinalDoubleButton) {
        prepareFinalDoubleButton.addEventListener('click', async () => {
            try {
                showError('');
                const nextGame = await requestJson(prepareFinalDoubleUrl, { method: 'POST' });
                selectedSource = null;
                receiveGameState(nextGame, 'own');
                lastDiceKey = JSON.stringify(game.dice || []);
            } catch (error) {
                showError(error.message);
            }
        });
    }

    if (debugGameTools && prepareExtraHeadMoveButton) {
        prepareExtraHeadMoveButton.addEventListener('click', async () => {
            try {
                showError('');
                const nextGame = await requestJson(prepareExtraHeadMoveUrl, { method: 'POST' });
                selectedSource = null;
                receiveGameState(nextGame, 'own');
                lastDiceKey = JSON.stringify(game.dice || []);
            } catch (error) {
                showError(error.message);
            }
        });
    }

    if (debugGameTools && prepareBlockingEventButton) {
        prepareBlockingEventButton.addEventListener('click', async () => {
            try {
                showError('');
                const nextGame = await requestJson(prepareBlockingEventUrl, { method: 'POST' });
                selectedSource = null;
                receiveGameState(nextGame, 'own');
                lastDiceKey = JSON.stringify(game.dice || []);
            } catch (error) {
                showError(error.message);
            }
        });
    }

    loadState();
    window.setInterval(renderCurrentGameDuration, 1000);
    connectStateSocket();
}());
