function Lunar_Blast_v45_trylsl()

COM_PORT = 'COM3';
BAUD_RATE = 9600;

%LUMO FNIRS synchronization settings
LUMO_BAUD_RATE = 9600;

TRIG_TASK_START      = 1;
TRIG_EPOCH_OBSERVE   = 2;
TRIG_EPOCH_TRACK     = 3;
TRIG_REST            = 4;
TRIG_DEFLECTED       = 5;
TRIG_IMPACT          = 6;
TRIG_OBS_END         = 7;
TRIG_TASK_COMPLETE   = 8;

% Default Task Settings
DEFAULT_CONFIG_FILE = 'lunar_config_2.csv';
DEFAULT_TRIAL_DUR   = 14.0;
DEFAULT_REST_DUR    = 6.0;
DEFAULT_TRACK_THRESH = 0.25;
DEFAULT_SHOW_PATH    = false;
PRE_EPOCH_PAUSE_DUR  = 1.0;  % seconds: visible hold before asteroid motion starts
ALIGN_COUNTDOWN_DUR  = 3.0;  % seconds: dial-alignment countdown before tracking epochs
FPS                  = 30;

ASTEROID_R = 2.5;
XL = [0 100];
YL = [0 65];
EARTH_X = 90;
EARTH_Y = 30;
MOON_X = 10;
MOON_Y = 35;
LASER_BARREL_LEN = 8;
AST_SPAWN_X = 50;
AST_SPAWN_Y = 30;

% Subject ID 
subjectID = '';
subjectID_clean = '';
selectedPlanet = [];


scriptFullPath = mfilename('fullpath');
scriptDir = fileparts(scriptFullPath);
if isempty(scriptDir)
    scriptDir = pwd;
end

rootDataDir = '';
runID = '';
subjectDir = '';

% Show Subject ID dialog before main figure
subjectID = showSubjectIDDialog();
if isempty(subjectID)
    % User closed / cancelled the dialog — abort launch.
    return;
end
subjectID_clean = regexprep(subjectID, '[^\w\-]', '_');

SPLASH_IMAGE_PATH = 'graphic.png';

% Load or create epoch configuration
if ~exist(DEFAULT_CONFIG_FILE, 'file')
    createDefaultConfig(DEFAULT_CONFIG_FILE, DEFAULT_TRIAL_DUR, DEFAULT_REST_DUR, DEFAULT_TRACK_THRESH);
end

configFile = DEFAULT_CONFIG_FILE;
cfg = loadEpochConfig(configFile, DEFAULT_TRIAL_DUR, DEFAULT_REST_DUR, DEFAULT_TRACK_THRESH, DEFAULT_SHOW_PATH);


% Mutable state
serialObj = [];
serialTimer = [];
animTimer = [];
lumoObj = [];          % LUMO fNIRS sync serial handle

rawValue = 500;

taskRunning = false;
epochRunning = false;
preEpochHold = false;
inAlignCountdown = false;
inRest = false;
feedbackRunning = false;

currentEpochIdx = 0;
currentEpochNumber = NaN;
currentWaveFreq = 1.0;
currentShowLaser = true;
currentShowPath = DEFAULT_SHOW_PATH;
currentTrialDur = DEFAULT_TRIAL_DUR;
currentRestDur = DEFAULT_REST_DUR;
currentTrackThresh = DEFAULT_TRACK_THRESH;
currentEarthX = EARTH_X;
currentEarthY = EARTH_Y;

phaseStart = tic;
epochStartClock = [];

trialFrames = 0;
contactFrames = 0;
epochLog = {};
summaryRows = {};

% Single persistent figure - welcome screen and game
fig = figure( ...
    'Name', 'Lunar Blast', ...
    'Color', [0 0 0], ...
    'NumberTitle', 'off', ...
    'MenuBar', 'none', ...
    'ToolBar', 'none', ...
    'Resize', 'on', ...
    'Position', [80 60 1200 720], ...
    'CloseRequestFcn', @onClose);

% Game axes -- hidden until welcome is complete
ax = axes('Parent', fig, ...
    'Position', [0.02 0.02 0.96 0.96], ...
    'Color', [0 0 0], ...
    'XColor', 'none', ...
    'YColor', 'none', ...
    'XLim', XL, ...
    'YLim', YL, ...
    'DataAspectRatioMode', 'manual', ...
    'DataAspectRatio', [1 1 1], ...
    'Visible', 'off');
hold(ax, 'on');

% Placeholder game handles - populated in buildGameScene after welcome
hEarth      = gobjects(0);
hPath       = gobjects(1);
hAstGlow    = gobjects(1);
hAst        = gobjects(1);
hAstTail    = gobjects(1);
hImpact     = gobjects(1);
hFlash      = gobjects(1);
hRect       = gobjects(1);
hBall       = gobjects(1);
hGun        = gobjects(0);
hBeam       = gobjects(1);
hBeamGlow   = gobjects(1);
hAlignLine  = gobjects(1);
hCountdownTxt = gobjects(1);
hRestBg     = gobjects(1);
hRestCross  = gobjects(1);
hResultTxt  = gobjects(1);
hConditionTxt = gobjects(1);

% Draw the splash screen (if image path was provided) - game overview
if ~isempty(SPLASH_IMAGE_PATH) && exist(SPLASH_IMAGE_PATH, 'file')
    showSplashScreen(SPLASH_IMAGE_PATH);
else
    showWelcomeOverlay();
end

% Displays splash screen; clicking start moves on to welcome screen
    function showSplashScreen(imagePath)
        sax = axes('Parent', fig, ...
            'Position', [0 0 1 1], ...
            'Color', [0 0 0], 'XColor', 'none', 'YColor', 'none', ...
            'XLim', [0 1], 'YLim', [0 1]);
        hold(sax, 'on');
    
        % Load and display the image, centered and scaled to fit.
        try
            img = imread(imagePath);
            % Place image so it fills most of the axes area with a small margin.
            imagesc(sax, [0.02 0.98], [0.02 0.98], flipud(img));
        catch
            % If the image fails to load, show a placeholder message.
            text(sax, 0.5, 0.55, {'Image could not be loaded.', imagePath}, ...
                'Color', [0.8 0.4 0.4], 'FontSize', 12, ...
                'HorizontalAlignment', 'center', 'FontName', 'Courier New');
        end
    
        % Flip Y so the image is not upside-down (image() sets YDir to reverse).
        set(sax, 'XLim', [0 1], 'YLim', [0 1], 'YDir', 'normal');
    
        % Dark semi-transparent footer strip behind the button.
        fill(sax, [0 1 1 0], [0 0 0.13 0.13], [0 0 0], ...
            'EdgeColor', 'none', 'FaceAlpha', 0.65);
    
        % START button — drawn as an axes patch so it matches the space theme.
        bx = [0.35 0.65 0.65 0.35];
        by = [0.025 0.025 0.095 0.095];
        hStartPatch = fill(sax, bx, by, [0.15 0.40 0.75], ...
            'EdgeColor', [0.50 0.75 1.00], 'LineWidth', 2.0, ...
            'ButtonDownFcn', @onStartClicked);
        hStartTxt = text(sax, 0.50, 0.060, 'START', ...
            'Color', [1 1 1], 'FontSize', 16, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New', ...
            'ButtonDownFcn', @onStartClicked);
        uistack(hStartPatch, 'top');
        uistack(hStartTxt,   'top');
    
        % ---- callback ----
        function onStartClicked(~,~)
            % Remove the splash axes and open the planet-select screen.
            try delete(sax); catch; end
            showWelcomeOverlay();
        end
    end

% Welcome overlay
    function showWelcomeOverlay()
        % Welcome serial state
        wSerialObj = [];
        wSerialTimer = [];
        wDialRaw = 500;
        
        try 
            wSerialObj = serialport(COM_PORT, BAUD_RATE);
            configureTerminator(wSerialObj, "LF");
            flush(wSerialObj);
            wSerialTimer = timer('ExecutionMode', 'fixedRate', 'Period', 0.002, ...
                'TimerFcn', @readWelcomeDial,'ErrorFcn', @(~,~) stopWelcomeSerial());
            start(wSerialTimer);
        catch
            % no serial during welcome - dial stays at default
        end
    
        % Welcome axes fills the whole figure
        wax = axes('Parent', fig, ...
            'Position', [0 0 1 1], ...
            'Color', [0 0 0], 'XColor', 'none', 'YColor', 'none', ...
            'XLim', [0 120], 'YLim', [0 75], ...
            'DataAspectRatioMode', 'manual', 'DataAspectRatio', [1 1 1]);
        hold(wax, 'on');
    
        % Starfield
        rng(42);
        scatter(wax, rand(1,350)*120, rand(1,350)*75, rand(1,350)*3+0.3, ...
            'w', 'filled', 'MarkerFaceAlpha', 0.45);
    
        % Title
        text(wax, 60, 69, 'LUNAR BLAST', 'Color', [0.9 0.9 1], 'FontSize', 34, ...
            'FontWeight', 'bold', 'HorizontalAlignment', 'center', 'FontName', 'Courier New');
        text(wax, 60, 64, 'Turn dial to select planet', ...
            'Color', [0.6 0.7 0.9], 'FontSize', 20, 'HorizontalAlignment', 'center', 'FontName', 'Courier New');
    
        % Planet preview centers
        pCx = [24 60 96];
        pCy = [38 38 38];
        pNames = {'Earth', 'Mars', 'Ice Giant'};
        pDrawFns = {@drawEarth, @drawMars, @drawIceGiant};
    
        for p = 1:3
            pDrawFns{p}(wax, pCx(p), pCy(p));
            text(wax, pCx(p), 25, pNames{p}, 'Color', [0.85 0.85 1], 'FontSize', 25, ...
                'FontWeight', 'bold', 'HorizontalAlignment', 'center', 'FontName', 'Courier New');
        end
    
        % Selection ring + label
        th = linspace(0, 2*pi, 120);
        hRing = plot(wax, pCx(1) + 9.5*cos(th), pCy(1) + 9.5*sin(th), ...
            '-', 'Color', [0.4 1 0.6], 'LineWidth', 2.5);
        hSelLabel = text(wax, pCx(1), 19.5, '▲  SELECTED  ▲', ...
            'Color', [0.4 1 0.6], 'FontSize', 9, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New');
    
        % Dial position indicator bar
        fill(wax, [8 112 112 8], [14 14 16.5 16.5], [0.12 0.12 0.18], ...
            'EdgeColor', [0.3 0.3 0.5], 'LineWidth', 1);
        plot(wax, [8+(341/1023)*104, 8+(341/1023)*104], [13.5 17], '-', 'Color', [0.4 0.4 0.6], 'LineWidth', 1);
        plot(wax, [8+(683/1023)*104, 8+(683/1023)*104], [13.5 17], '-', 'Color', [0.4 0.4 0.6], 'LineWidth', 1);
        hDialMarker = fill(wax, [0 0 0 0], [14 14 16.5 16.5], [0.4 1 0.6], 'EdgeColor', 'none');
    
        % BEGIN button (axes patch so it matches the welcome style)
        bx2 = [44 76 76 44]; by2 = [2.5 2.5 6.5 6.5];
        hBtnPatch = fill(wax, bx2, by2, [0.15 0.4 0.75], ...
            'EdgeColor', [0.5 0.75 1], 'LineWidth', 1.5, 'ButtonDownFcn', @onBegin);
        hBtnTxt = text(wax, 60, 4.5, 'BEGIN', 'Color', [1 1 1], 'FontSize', 14, ...
            'FontWeight', 'bold', 'HorizontalAlignment', 'center', ...
            'FontName', 'Courier New', 'ButtonDownFcn', @onBegin);
        uistack(hBtnPatch, 'top');
        uistack(hBtnTxt,   'top');
    
        % Dial polling timer
        lastZone = dialZone(wDialRaw);
        updateWelcomeDisplay(lastZone);
    
        pollTimer = timer('ExecutionMode', 'fixedRate', 'Period', 0.05, ...
            'TimerFcn', @pollWelcomeDial);
        start(pollTimer);
    
        % ---- nested helpers ----
        function pollWelcomeDial(~,~)
            if ~isvalid(fig); return; end
            z = dialZone(wDialRaw);
            updateWelcomeDisplay(z);
            drawnow limitrate;
        end
    
        function updateWelcomeDisplay(z)
            set(hRing, 'XData', pCx(z) + 9.5*cos(th), 'YData', pCy(z) + 9.5*sin(th));
            set(hSelLabel, 'Position', [pCx(z), 19.5, 0]);
            markerX = 8 + ((1023 - wDialRaw) / 1023) * 104;
            % markerX = 8 + ((wDialRaw) / 1023) * 104;
            mw = 1.4;
            set(hDialMarker, ...
                'XData', [markerX-mw markerX+mw markerX+mw markerX-mw], ...
                'YData', [13.8 13.8 16.7 16.7]);
        end
    
        function readWelcomeDial(~,~)
            try
                if ~isempty(wSerialObj) && isvalid(wSerialObj) && ...
                        wSerialObj.NumBytesAvailable > 0
                    ln = readline(wSerialObj);
                    val = str2double(strtrim(ln));
                    if ~isnan(val)
                        % wDialRaw = max(0, min(1023, 1023 -val));
                        wDialRaw = max(0, min(1023, val));
                    end
                end
            catch
            end
        end
    
        function stopWelcomeSerial()
            try stop(wSerialTimer);  delete(wSerialTimer);  wSerialTimer = []; catch; end
            try delete(wSerialObj);                         wSerialObj   = []; catch; end
        end
    
        function onBegin(~,~)
            % subjectID was already collected from the popup dialog before
            % the main window opened — nothing to read here.
            selectedPlanet = dialZone(wDialRaw);
    
            % Stop welcome serial + poll timer.
            stopWelcomeSerial();
            try stop(pollTimer); delete(pollTimer); catch; end
    
            % Remove welcome axes.
            try delete(wax); catch; end
    
            % Build the game scene and start.
            setupAfterWelcome();
        end
    end

% Post-welcome setup - creates folders, builds game scene, starts task
    function setupAfterWelcome()
        % create output folders now that subjectID is known
        runID       = datestr(now, 'yyyymmdd_HHMMSS');
        rootDataDir = fullfile(scriptDir, 'LunarBlast_Data');
        subjectDir  = fullfile(rootDataDir, subjectID_clean);

        [okRoot, rootMsg] = ensureFolder(rootDataDir);
        [okSubj, subjMsg] = ensureFolder(subjectDir);
        if ~(okRoot && okSubj)
            rootDataDir = fullfile(pwd, 'LunarBlast_Data');
            subjectDir  = fullfile(rootDataDir, subjectID_clean);
            [okRoot, rootMsg] = ensureFolder(rootDataDir);
            [okSubj, subjMsg] = ensureFolder(subjectDir);
        end
        if ~(okRoot && okSubj)
            rootDataDir = fullfile(tempdir, 'LunarBlast_Data');
            subjectDir  = fullfile(rootDataDir, subjectID_clean);
            [okRoot, rootMsg] = ensureFolder(rootDataDir); %#ok<ASGLU>
            [okSubj, subjMsg] = ensureFolder(subjectDir);  %#ok<ASGLU>
        end

        % Copy config for audit trail.
        try
            [~, cfgBase, cfgExt] = fileparts(configFile);
            copyfile(configFile, fullfile(subjectDir, ['USED_' runID '_' cfgBase cfgExt]));
        catch
        end

        buildGameScene();

        % Auto-connect serial and start task after figure finishes rendering.
        autoStartTimer = timer('StartDelay', 0.5, 'ExecutionMode', 'singleShot', ...
            'TimerFcn', @(~,~) autoConnectAndStart());
        start(autoStartTimer);
    end

% Build game scene into ax (called once, after welcome)
    function buildGameScene()
        % make the game aces visible and populate all graphic handles
        set(ax, 'Visible', 'off'); % aces frame stays hidden; contents are visible
        
        % APRIL TAGS -------------------------------------
        % ============================================================
        % APRILTAG CORNER MARKERS
        % ============================================================
        % Each tag should be an image file containing an AprilTag.
        % Position is specified in the game's 0-100 x/y coordinate system.

        tagSize = 8;   % Size of each tag in game-coordinate units

        % Load AprilTag images
        tagTL = imread('apriltag_TL.png');
        tagTR = imread('apriltag_TR.png');
        tagBL = imread('apriltag_BL.png');
        tagBR = imread('apriltag_BR.png');

        % Bottom-left
        hTagBL = image(ax, ...
            [0 tagSize], [0 tagSize], tagBL);
        
        % Bottom-right
        hTagBR = image(ax, ...
            [100-tagSize 100], [0 tagSize], tagBR);
        
        % Top-left
        hTagTL = image(ax, ...
            [0 tagSize], [65-tagSize 65], tagTL);
        
        % Top-right
        hTagTR = image(ax, ...
            [100-tagSize 100], [65-tagSize 65], tagTR);

        

        % Keep the tags on top of the game graphics
        uistack(hTagTL, 'top');
        uistack(hTagTR, 'top');
        uistack(hTagBL, 'top');
        uistack(hTagBR, 'top');

        %------------------------------------------

        drawStars(ax);
        hEarth = drawPlanet(ax, currentEarthX, currentEarthY, selectedPlanet);

        hPath     = plot(ax, NaN, NaN, '--', 'Color', [0.35 0.35 0.5], 'LineWidth', 1, 'Visible', 'off');
        hAstGlow  = fill(ax, NaN, NaN, [1 0.4 0.1], 'EdgeColor', 'none', 'FaceAlpha', 0.3);
        hAst      = fill(ax, NaN, NaN, [0.85 0.85 0.85], 'EdgeColor', [1 0.5 0.2], 'LineWidth', 1.5);
        hAstTail  = plot(ax, NaN, NaN, '-', 'Color', [1 0.35 0.1], 'LineWidth', 3);
        hImpact   = scatter(ax, NaN, NaN, 80, [1 1 0], 'filled', 'MarkerFaceAlpha', 0.9, 'Marker', '*');
        hFlash    = fill(ax, NaN, NaN, [1 0.8 0], 'EdgeColor', 'none', 'FaceAlpha', 0);

        [hRect, hBall, ~, ~, hBeam, hBeamGlow, hGun] = drawLauncher(ax, 10, 35, 45);

        % Rest screen overlay
        hRestBg   = fill(ax, [0 100 100 0], [0 0 65 65], [0.15 0.15 0.15], ...
            'EdgeColor', 'none', 'Visible', 'off');
        hRestCross = plot(ax, [48 52 NaN 50 50], [32.5 32.5 NaN 30.5 34.5], ...
            'w-', 'LineWidth', 2.5, 'Visible', 'off');
        uistack(hRestBg,    'top');
        uistack(hRestCross, 'top');

        % Alignment guide line — dotted, shown only during tracking countdown.
        hAlignLine = plot(ax, NaN, NaN, ':', 'Color', [1 0.85 0.2], 'LineWidth', 2.2, 'Visible', 'off');

        % Large countdown number shown during alignment phase.
        hCountdownTxt = text(ax, AST_SPAWN_X, AST_SPAWN_Y + 8, '', ...
            'Color', [1 0.85 0.2], 'FontSize', 28, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New', 'Visible', 'off');

        hResultTxt = text(ax, 50, 45, '', ...
            'Color', [1 0.9 0], 'FontSize', 26, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New', 'Visible', 'off');

        hConditionTxt = text(ax, 50, 45, '', ...
            'Color', [1 1 1], 'FontSize', 36, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New', 'Visible', 'off');

        updateLauncherGraphic(rawValue);
    end

% Serial
    function connectSerial(~,~)
        disconnectSerial();
        try
            serialObj = serialport(COM_PORT, BAUD_RATE);
            configureTerminator(serialObj, 'LF');
            flush(serialObj);
            serialTimer = timer('ExecutionMode', 'fixedRate', 'Period', 0.0001, ...
                'TimerFcn', @readSerial, 'ErrorFcn', @serialTimerError);
            start(serialTimer);
        catch ME
            warning('Lunar_Blast:serialConnect', 'Serial error: %s', ME.message);
        end
    end

    function disconnectSerial(~,~)
        if ~isempty(serialTimer) && isvalid(serialTimer)
            stop(serialTimer);
            delete(serialTimer);
            serialTimer = [];
        end
        if ~isempty(serialObj) && isvalid(serialObj)
            delete(serialObj);
            serialObj = [];
        end
    end

    function readSerial(~,~)
        try
            if ~isempty(serialObj) && serialObj.NumBytesAvailable > 0
                line = readline(serialObj);
                val = str2double(strtrim(line));
                if ~isnan(val)
                    val = max(0, min(1023, val));
                    % rawValue = 1023 - val;
                    rawValue = val;
                    if isvalid(fig)
                        updateLauncherGraphic(rawValue);
                    end
                end
            end
        catch
        end
    end

    function serialTimerError(~,~)
        disconnectSerial();
    end

    function autoConnectAndStart(~,~)
        % Silently attempt dial serial connection, then start the task regardless.
        try
            connectSerial();
        catch
            % Serial not available; task still runs (dial input ignored / uses default).
        end
        % Connect LUMO sync port (last available free port).
        connectLumo();
        startTask();
    end

% ---------------------------------------------------------------
%  STUFF I DO NOT NEED TO LOOK AT
% ---------------------------------------------------------------


    % ------------------------------------------------------------------
    %  LUMO fNIRS sync helpers
    % ------------------------------------------------------------------
    function connectLumo()
        disconnectLumo();
        try
            ports = serialportlist('available');
            if isempty(ports)
                return;
            end
            lumoPort = ports(end);
            lumoObj  = serialport(lumoPort, LUMO_BAUD_RATE);
        catch ME
            warning('Lunar_Blast:lumoConnect', 'LUMO connect failed: %s', ME.message);
        end
    end

    function disconnectLumo()
        if ~isempty(lumoObj) && isvalid(lumoObj)
            try delete(lumoObj); catch; end
            lumoObj = [];
        end
    end

    function sendLumoSync(trigCode)
        % Send ASCII trigger as uint16 to LUMO laptop.
        % Mirrors: write(serialhandle, synch.ascii(n), "uint16")
        if isempty(lumoObj) || ~isvalid(lumoObj)
            return;
        end
        try
            write(lumoObj, uint16(trigCode), 'uint16');
        catch
            % Do not let a sync failure interrupt the task.
        end
    end

%% ========================================================================
%  Task control
% ========================================================================
    function startTask(~,~)
        if taskRunning
            return;
        end

        taskRunning = true;
        epochRunning = false;
        preEpochHold = false;
        inAlignCountdown = false;
        inRest = false;
        feedbackRunning = false;
        currentEpochIdx = 0;

        sendLumoSync(TRIG_TASK_START);
        beginNextEpoch();
    end

    function beginNextEpoch()
        if ~taskRunning
            return;
        end

        currentEpochIdx = currentEpochIdx + 1;

        if currentEpochIdx > height(cfg)
            finishTask();
            return;
        end

        row = cfg(currentEpochIdx, :);

        currentEpochNumber = row.epoch;
        currentWaveFreq = row.waveFreq;
        currentShowLaser = row.SHOW_LASER;
        currentRestDur = row.REST_DURATION;
        currentTrialDur = row.TRIAL_DURATION;
        currentTrackThresh = row.TRACKING_THRESHOLD;
        currentShowPath = row.SHOW_ASTEROID_PATH;

        epochRunning = false;
        preEpochHold = false;
        inAlignCountdown = false;
        inRest = false;
        feedbackRunning = false;

        trialFrames = 0;
        contactFrames = 0;
        epochLog = {};
        epochStartClock = [];

        hideRestScreen();
        hideAlignGuide();
        set(hResultTxt, 'Visible', 'off');

        updatePlanetForCurrentEpoch();
        drawPathForCurrentEpoch();
        applyLaserVisibility();

        % Show large condition label at centre of screen for the pre-epoch hold.
        if currentShowLaser
            set(hConditionTxt, 'String', 'Set Up Laser for Tracking', 'Color', [0.35 1 0.55]);
        else
            set(hConditionTxt, 'String', 'Watching Only', 'Color', [0.85 0.85 1.0]);
        end
        set(hConditionTxt, 'Visible', 'on');

        % Show the asteroid stationary at spawn.
        drawAsteroidAt(AST_SPAWN_X, AST_SPAWN_Y, false);
        set(hImpact, 'XData', NaN, 'YData', NaN);

        % Both tracking and observation epochs use a 3-second countdown before motion.
        inAlignCountdown = true;
        if currentShowLaser
            % Show alignment guide line during tracking countdown.
            updateAlignGuide();
        end
        set(hCountdownTxt, 'String', sprintf('%d', ceil(ALIGN_COUNTDOWN_DUR)), ...
            'Position', [AST_SPAWN_X, AST_SPAWN_Y + 8, 0], 'Visible', 'on');

        phaseStart = tic;
        startAnim();
    end

    function finishTask()
        taskRunning = false;
        epochRunning = false;
        preEpochHold = false;
        inAlignCountdown = false;
        inRest = false;
        feedbackRunning = false;
        stopAnim();
        hideAsteroid();
        hideRestScreen();
        hideAlignGuide();
        set(hPath, 'XData', NaN, 'YData', NaN, 'Visible', 'off');
        set(hImpact, 'XData', NaN, 'YData', NaN);
        set(hBeam,     'Visible', 'off');
        set(hBeamGlow, 'Visible', 'off');
        set(hGun,      'Visible', 'off');
        set(hConditionTxt, 'Visible', 'off');
        sendLumoSync(TRIG_TASK_COMPLETE);
        doTaskCompleteCelebration();
    end

    function doTaskCompleteCelebration()
        % Kid-friendly fireworks celebration shown after all epochs finish.
        % Runs a self-contained timer-driven loop; does not block the UI.

        CELEB_DUR   = 8.0;   % total celebration seconds
        NUM_BURSTS  = 6;     % firework bursts alive at once
        BURST_RAYS  = 22;    % rays per burst

        burstColors = {[1 0.25 0.25],[1 0.7 0],[0.2 1 0.35], ...
                       [0.3 0.7 1],[1 0.4 1],[1 1 0.3],[0.4 1 1]};

        % Pre-create patch handles for each burst (one patch per burst).
        hBursts = gobjects(NUM_BURSTS, 1);
        for b = 1:NUM_BURSTS
            hBursts(b) = fill(ax, NaN, NaN, [1 1 1], 'EdgeColor', 'none', ...
                'FaceAlpha', 0.9, 'Visible', 'off');
        end

        % Star / sparkle scatter overlay.
        hSparkles = scatter(ax, NaN, NaN, 60, [1 1 1], 'filled', ...
            'MarkerFaceAlpha', 0.85, 'Marker', 'p', 'Visible', 'off');

        % Big celebration text lines.
        hCelebTitle = text(ax, 50, 55, 'MISSION COMPLETE!', ...
            'Color', [1 1 0.3], 'FontSize', 30, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New', ...
            'Visible', 'off');
        hCelebSub = text(ax, 50, 47, 'AMAZING JOB, SPACE DEFENDER!', ...
            'Color', [0.4 1 1], 'FontSize', 18, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New', ...
            'Visible', 'off');
        hCelebStars = text(ax, 50, 40, '★  ★  ★  ★  ★', ...
            'Color', [1 0.65 0], 'FontSize', 22, 'FontWeight', 'bold', ...
            'HorizontalAlignment', 'center', 'FontName', 'Courier New', ...
            'Visible', 'off');

        % State for each burst: [cx, cy, radius, maxR, colorIdx, phase_start]
        burstState = zeros(NUM_BURSTS, 6);
        rng('shuffle');

        function spawnBurst(b)
            cx = 10 + rand*80;
            cy = 10 + rand*50;
            maxR = 6 + rand*10;
            ci = randi(numel(burstColors));
            burstState(b,:) = [cx, cy, 0, maxR, ci, toc(phaseStart)];
            set(hBursts(b), 'FaceColor', burstColors{ci}, 'Visible', 'on');
        end

        function drawBurst(b)
            cx  = burstState(b,1);
            cy  = burstState(b,2);
            age = toc(phaseStart) - burstState(b,6);
            maxR = burstState(b,4);
            ci   = burstState(b,5);
            burstDur = 0.7;
            frac = min(age / burstDur, 1);
            % ease out
            r = maxR * (1 - (1-frac)^2);
            alpha = max(0, 1 - frac*1.4);

            % Build star-burst polygon from rays.
            angles = linspace(0, 2*pi, BURST_RAYS+1);
            angles = angles(1:end-1);
            innerR = r * 0.35;
            % Alternate outer/inner radii for starburst shape.
            allR = repmat([r; innerR], 1, ceil(BURST_RAYS/2));
            allR = allR(1:BURST_RAYS);
            px2 = cx + allR .* cos(angles);
            py2 = cy + allR .* sin(angles);

            set(hBursts(b), 'XData', px2, 'YData', py2, ...
                'FaceColor', burstColors{ci}, 'FaceAlpha', alpha);

            if frac >= 1
                set(hBursts(b), 'Visible', 'off');
                spawnBurst(b);   % recycle immediately
            end
        end

        % Show text and spawn initial bursts.
        set(hCelebTitle, 'Visible', 'on');
        set(hCelebSub,   'Visible', 'on');
        set(hCelebStars, 'Visible', 'on');
        
        phaseStart = tic;
        for b = 1:NUM_BURSTS
            pause(0.05);   % stagger initial spawns slightly
            spawnBurst(b);
        end

        celebTimer = timer('ExecutionMode', 'fixedRate', 'Period', 1/FPS, ...
            'TimerFcn', @celebStep, 'ErrorFcn', @(~,~) cleanupCeleb());
        start(celebTimer);

        function celebStep(~,~)
            if ~isvalid(fig)
                cleanupCeleb();
                return;
            end
            t = toc(phaseStart);

            % Pulse the title colour through a rainbow.
            hue = mod(t * 0.6, 1);
            set(hCelebTitle, 'Color', hsv2rgb([hue, 0.9, 1.0]));

            % Animate each burst.
            for bb = 1:NUM_BURSTS
                drawBurst(bb);
            end

            % Sparkle cloud — random new positions each frame.
            nsp = 18;
            set(hSparkles, ...
                'XData', 5 + rand(1,nsp)*90, ...
                'YData', 5 + rand(1,nsp)*55, ...
                'CData', rand(nsp,3), ...
                'Visible', 'on');

            drawnow limitrate;

            if t >= CELEB_DUR
                cleanupCeleb();
            end
        end

        function cleanupCeleb()
            try stop(celebTimer); delete(celebTimer); catch; end
            if ~isvalid(fig); return; end
            % Replace with a calm "well done" message.
            for bb = 1:NUM_BURSTS
                try set(hBursts(bb), 'Visible', 'off'); catch; end
            end
            try set(hSparkles,   'Visible', 'off'); catch; end
            try set(hCelebSub,   'Visible', 'off'); catch; end
            try set(hCelebStars, 'Visible', 'off'); catch; end
            try
                set(hCelebTitle, 'String', 'MISSION COMPLETE!', ...
                    'Color', [0.3 1 0.4], 'FontSize', 26);
            catch 
            end
        end
    end

%% ========================================================================
%  Animation
% ========================================================================
    function startAnim()
        stopAnim();
        animTimer = timer('ExecutionMode', 'fixedRate', 'Period', 1/FPS, ...
            'TimerFcn', @animStep, 'ErrorFcn', @(~,~) stopAnim());
        start(animTimer);
    end

    function stopAnim()
        if ~isempty(animTimer) && isvalid(animTimer)
            stop(animTimer);
            delete(animTimer);
            animTimer = [];
        end
    end

    function animStep(~,~)
        if ~isvalid(fig)
            stopAnim();
            return;
        end

        elapsed = toc(phaseStart);

        if inAlignCountdown
            remaining = max(0, ALIGN_COUNTDOWN_DUR - elapsed);
            countNum = ceil(remaining);
            set(hCountdownTxt, 'String', sprintf('%d', countNum));
            % Keep the live laser beam tracking the dial each frame (tracking only).
            if currentShowLaser
                updateLauncherGraphic(rawValue);
            end
            if elapsed >= ALIGN_COUNTDOWN_DUR
                % Countdown done — hide guide, enter the standard pre-epoch hold.
                inAlignCountdown = false;
                preEpochHold = true;
                hideAlignGuide();
                phaseStart = tic;
            end

        elseif preEpochHold
            if currentShowLaser
                updateLauncherGraphic(rawValue);
            end
            if elapsed >= PRE_EPOCH_PAUSE_DUR
                beginEpochMovement();
            end

        elseif epochRunning
            if elapsed >= currentTrialDur
                endEpoch();
            else
                doEpochFrame(elapsed);
            end

        elseif feedbackRunning
            if elapsed >= 1.5
                feedbackRunning = false;
                if currentRestDur > 0
                    beginRest();
                else
                    beginNextEpoch();
                end
            end

        elseif inRest
            if elapsed >= currentRestDur
                inRest = false;
                hideRestScreen();
                beginNextEpoch();
            end
        end

        drawnow limitrate;
    end

    function beginEpochMovement()
        % Start the actual 14-s analyzable movement window after the pre-epoch hold.
        % Data logging begins here, so the 1-s hold is not included in epoch timeseries metrics.
        preEpochHold = false;
        epochRunning = true;
        trialFrames = 0;
        contactFrames = 0;
        epochLog = {};
        epochStartClock = now;
        phaseStart = tic;

        % Send LUMO sync trigger immediately before asteroid motion begins.
        if currentShowLaser
            sendLumoSync(TRIG_EPOCH_TRACK);
        else
            sendLumoSync(TRIG_EPOCH_OBSERVE);
        end

        % Hide the condition label now that the trial has started.
        set(hConditionTxt, 'Visible', 'off');
    end

    function doEpochFrame(elapsed)
        trialFrames = trialFrames + 1;
        t = min(elapsed / currentTrialDur, 1);

        osc_amp = 30;
        ast_x = AST_SPAWN_X + (currentEarthX - AST_SPAWN_X) * t;
        ast_y = AST_SPAWN_Y + osc_amp * -(2/pi) * asin(sin(2 * pi * currentWaveFreq * t));

        [laser_x, laser_y, dist] = beamClosestPoint(rawValue, ast_x, ast_y);
        contact = dist <= ASTEROID_R;

        % During observation-only epochs, the laser is hidden and contact is not
        % used to determine feedback. The underlying dial/laser geometry is still
        % logged for quality control if the dial is moved.
        if currentShowLaser && contact
            contactFrames = contactFrames + 1;
            set(hImpact, 'XData', laser_x, 'YData', laser_y);
        else
            set(hImpact, 'XData', NaN, 'YData', NaN);
        end

        drawAsteroidAt(ast_x, ast_y, currentShowLaser && contact);

        epochLog(end+1, :) = { ...
            subjectID, ...
            datestr(epochStartClock, 'yyyy-mm-dd HH:MM:SS.FFF'), ...
            currentEpochIdx, ...
            currentEpochNumber, ...
            conditionLabel(), ...
            currentShowLaser, ...
            currentWaveFreq, ...
            currentTrialDur, ...
            currentTrackThresh, ...
            elapsed, ...
            rawValue, ...
            ast_x, ...
            ast_y, ...
            laser_x, ...
            laser_y, ...
            dist, ...
            double(contact), ...
            double(currentShowLaser && contact)};
    end

    function endEpoch()
        stopAnim();
        epochRunning = false;
        preEpochHold = false;

        totalExpectedFrames = max(1, currentTrialDur * FPS);
        finalPct = contactFrames / totalExpectedFrames;

        if currentShowLaser
            deflected = finalPct >= currentTrackThresh;
        else
            deflected = false;
        end

        saveCurrentEpoch(finalPct, deflected);

        set(hPath, 'XData', NaN, 'YData', NaN, 'Visible', 'off');
        set(hImpact, 'XData', NaN, 'YData', NaN);

        if currentShowLaser && deflected
            % Hide laser beam before any animation so it doesn't appear
            % as a stray line during the fly-away.
            set(hBeam,     'Visible', 'off');
            set(hBeamGlow, 'Visible', 'off');

            % --- Planet deflection glow FIRST ----------------------------
            sendLumoSync(TRIG_DEFLECTED);
            set(hResultTxt, 'String', 'DEFLECTED', 'Color', [0.3 1 0.4], 'Visible', 'on');
            doDeflectionFlash();

            % --- Asteroid fly-away AFTER glow ----------------------------
            osc_amp = 30;
            finalAstX = currentEarthX;
            finalAstY = AST_SPAWN_Y + osc_amp * -(2/pi) * asin(sin(2 * pi * currentWaveFreq));

            deflectDirX =  0.82;
            deflectDirY =  0.57;
            nFrames = round(FPS * 0.9);

            for df = 1:nFrames
                if ~isvalid(fig)
                    break;
                end
                frac = df / nFrames;
                ease = frac * frac * (3 - 2*frac);
                flyDist = ease * 55;
                ax_x = finalAstX + deflectDirX * flyDist;
                ax_y = finalAstY + deflectDirY * flyDist + 4 * sin(pi * frac);

                tailLen = max(1, round(8 * (1 - frac)));
                tx = ax_x - deflectDirX * (1:tailLen);
                ty = ax_y - deflectDirY * (1:tailLen);
                set(hAstTail, 'XData', tx, 'YData', ty, 'Visible', 'on');

                drawAsteroidAt(ax_x, ax_y, false);
                drawnow limitrate;
                pause(1 / FPS);
            end

            hideAsteroid();
            set(hAstTail, 'XData', NaN, 'YData', NaN, 'Visible', 'off');
        elseif currentShowLaser
            hideAsteroid();
            sendLumoSync(TRIG_IMPACT);
            set(hResultTxt, 'String', 'PLANET IMPACT', 'Color', [1 0.25 0.25], 'Visible', 'on');
        else
            hideAsteroid();
            sendLumoSync(TRIG_OBS_END);
            set(hResultTxt, 'String', 'WATCHING COMPLETE', 'Color', [0.8 0.8 0.8], 'Visible', 'on');
        end

        feedbackRunning = true;
        phaseStart = tic;
        startAnim();
    end

    function beginRest()
        hideAsteroid();
        set(hResultTxt, 'Visible', 'off');
        set(hPath, 'Visible', 'off');
        set(hImpact, 'XData', NaN, 'YData', NaN);
        set(hBeam,     'Visible', 'off');
        set(hBeamGlow, 'Visible', 'off');

        inRest = true;
        phaseStart = tic;

        % Send LUMO sync trigger immediately before fixation cross appears.
        sendLumoSync(TRIG_REST);

        set(hRestBg, 'Visible', 'on');
        set(hRestCross, 'Visible', 'on');

        startAnim();
    end

%% ========================================================================
%  Saving
% ========================================================================
    function saveCurrentEpoch(finalPct, deflected)
        % Robust per-epoch saving. This function intentionally avoids append-only
        % table writing inside the timer callback because that can fail silently
        % on some MATLAB installs or network/cloud folders.

        headers = {'SubjectID','EpochStart','EpochIndex','EpochNumber','Condition', ...
            'SHOW_LASER','waveFreq','TrialDuration_s','TrackingThreshold', ...
            'Time_s','DialRawValue','Asteroid_X','Asteroid_Y','Laser_X','Laser_Y', ...
            'Tracking_Error','GeometryContact','ScoredLaserContact'};

        if isempty(epochLog)
            epochLog = {subjectID, datestr(epochStartClock, 'yyyy-mm-dd HH:MM:SS.FFF'), ...
                currentEpochIdx, currentEpochNumber, conditionLabel(), currentShowLaser, ...
                currentWaveFreq, currentTrialDur, currentTrackThresh, NaN, rawValue, ...
                NaN, NaN, NaN, NaN, NaN, NaN, NaN};
        end

        [okFolder, folderMsg] = ensureFolder(subjectDir);
        if ~okFolder
            error('Could not create subject folder: %s', folderMsg);
        end

        cond = conditionLabel();
        safeCond = regexprep(lower(cond), '[^a-z0-9_\-]', '_');
        epochBase = sprintf('%s_%s_epoch_%03d_waveFreq_%0.2f_%s', ...
            subjectID_clean, runID, currentEpochIdx, currentWaveFreq, safeCond);
        epochCsvFile = fullfile(subjectDir, [epochBase '.csv']);
        epochMatFile = fullfile(subjectDir, [epochBase '.mat']);
        summaryCsvFile = fullfile(subjectDir, sprintf('%s_%s_epoch_summary.csv', subjectID_clean, runID));
        summaryMatFile = fullfile(subjectDir, sprintf('%s_%s_epoch_summary.mat', subjectID_clean, runID));

        % Save the full epoch timeseries first.
        saveOK = false;
        saveMsg = '';
        try
            T = cell2table(epochLog, 'VariableNames', headers);
            writetable(T, epochCsvFile);
            saveOK = true;
        catch MEcsv
            saveMsg = MEcsv.message;
            try
                T = cell2table(epochLog, 'VariableNames', headers); %#ok<NASGU>
                save(epochMatFile, 'T', 'epochLog', 'headers', 'subjectID', 'subjectID_clean', ...
                    'currentEpochIdx', 'currentEpochNumber', 'currentWaveFreq', 'currentShowLaser', ...
                    'currentTrialDur', 'currentRestDur', 'currentTrackThresh', 'finalPct', 'deflected');
                saveOK = true;
                epochCsvFile = epochMatFile;
            catch MEmat
                saveMsg = [saveMsg ' | MAT fallback failed: ' MEmat.message];
            end
        end

        if ~saveOK
            errordlg(sprintf('Epoch %d did not save.\n\n%s', currentEpochIdx, saveMsg), 'Lunar Blast Save Error');
            error('Epoch save failed: %s', saveMsg);
        end

        % Build one-line summary.
        nFrames = size(epochLog, 1);
        try
            scoredContactVals = cell2mat(epochLog(:, 18));
        catch
            scoredContactVals = NaN(nFrames,1);
        end
        try
            trackingErrorVals = cell2mat(epochLog(:, 16));
        catch
            trackingErrorVals = NaN(nFrames,1);
        end

        nContact = sum(scoredContactVals == 1);
        validErr = trackingErrorVals(~isnan(trackingErrorVals));
        if isempty(validErr)
            meanError = NaN;
        else
            meanError = mean(validErr);
        end

        summaryHeaders = {'SubjectID','EpochIndex','EpochNumber','Condition','SHOW_LASER', ...
            'waveFreq','TrialDuration_s','RestDurationAfter_s','TrackingThreshold', ...
            'TotalFrames','ContactFrames','ContactPct','MeanTrackingError','Deflected','EpochFile'};

        summaryRow = {subjectID, currentEpochIdx, currentEpochNumber, cond, currentShowLaser, ...
            currentWaveFreq, currentTrialDur, currentRestDur, currentTrackThresh, ...
            nFrames, nContact, finalPct * 100, meanError, double(deflected), epochCsvFile};

        % Rewrite the summary from accumulated rows instead of relying on
        % writetable(...,'WriteMode','append'), which is a common compatibility issue.
        try
            if ~exist('summaryRows', 'var') || isempty(summaryRows)
                summaryRows = {};
            end
        catch
            summaryRows = {};
        end
        summaryRows(end+1, :) = summaryRow;

        try
            S = cell2table(summaryRows, 'VariableNames', summaryHeaders);
            writetable(S, summaryCsvFile);
        catch MEsummary
            try
                save(summaryMatFile, 'summaryRows', 'summaryHeaders');
            catch
            end
            % Do not stop the task if only the summary fails. The epoch file is the critical file.
            return;
        end

    end

%% ========================================================================
%  Visual and geometry helpers
% ========================================================================
    function drawPathForCurrentEpoch()
        t_p = linspace(0, 1, 300);
        osc_amp = 30;
        px = AST_SPAWN_X + (currentEarthX - AST_SPAWN_X) .* t_p;
        py = AST_SPAWN_Y + osc_amp .* -(2/pi) .* asin(sin(2 * pi * currentWaveFreq .* t_p));
        set(hPath, 'XData', px, 'YData', py);
        if currentShowPath
            set(hPath, 'Visible', 'on');
        else
            set(hPath, 'Visible', 'off');
        end
    end

    function applyLaserVisibility()
        if currentShowLaser
            set(hBeam,     'Visible', 'on');
            set(hBeamGlow, 'Visible', 'on');
            set(hGun,      'Visible', 'on');
        else
            set(hBeam,     'Visible', 'off');
            set(hBeamGlow, 'Visible', 'off');
            set(hGun,      'Visible', 'off');
        end
    end

    function label = conditionLabel()
        if currentShowLaser
            label = 'TRACKING';
        else
            label = 'WATCHING';
        end
    end

    function updateAlignGuide()
        % Draw a static dotted guide line from the moon centre to the asteroid
        % spawn point. Called once when the countdown begins.
        set(hAlignLine, ...
            'XData', [MOON_X, AST_SPAWN_X], ...
            'YData', [MOON_Y, AST_SPAWN_Y], ...
            'Visible', 'on');
        % Keep the countdown label just above the asteroid spawn point.
        set(hCountdownTxt, 'Position', [AST_SPAWN_X, AST_SPAWN_Y + 8, 0]);
    end

    function hideAlignGuide()
        set(hAlignLine,    'XData', NaN, 'YData', NaN, 'Visible', 'off');
        set(hCountdownTxt, 'Visible', 'off');
    end

    function hideRestScreen()
        set(hRestBg, 'Visible', 'off');
        set(hRestCross, 'Visible', 'off');
    end

    function [cpx, cpy, dist] = beamClosestPoint(val, px, py)
        angle_deg = -70 + (val / 1023) * 140;
        rad = deg2rad(angle_deg);

        bx = MOON_X;
        by = MOON_Y;
        len = LASER_BARREL_LEN;

        ox = bx + cos(rad) * len;
        oy = by + sin(rad) * len;

        dx = cos(rad);
        dy = sin(rad);

        t = (px - ox) * dx + (py - oy) * dy;
        t = max(0, t);

        cpx = ox + t * dx;
        cpy = oy + t * dy;
        dist = sqrt((px - cpx)^2 + (py - cpy)^2);
    end

    function drawAsteroidAt(cx, cy, contact)
        th = linspace(0, 2*pi, 60);
        r = ASTEROID_R;

        set(hAst, 'XData', cx + r*cos(th), 'YData', cy + r*sin(th));

        gr = r * (1.6 + 0.6*double(contact));
        gc = [1 0.4 0.1] + [0 0.3 0.2]*double(contact);
        set(hAstGlow, 'XData', cx + gr*cos(th), 'YData', cy + gr*sin(th), ...
            'FaceColor', gc);
    end

    function hideAsteroid()
        set(hAst, 'XData', NaN, 'YData', NaN);
        set(hAstGlow, 'XData', NaN, 'YData', NaN);
        set(hAstTail, 'XData', NaN, 'YData', NaN);
    end

    function updateLauncherGraphic(val)
        angle_deg = -70 + (val/1023) * 140;

        bx = MOON_X;
        by = MOON_Y;
        len = LASER_BARREL_LEN;
        w = 1.2;

        rad = deg2rad(angle_deg);
        dx = cos(rad);
        dy = sin(rad);
        px = -sin(rad);
        py = cos(rad);

        % Rotating barrel mounted on the lunar base.
        turretX = [bx+dx*len+px*w, bx+dx*len-px*w, bx-dx*1.0-px*w, bx-dx*1.0+px*w];
        turretY = [by+dy*len+py*w, by+dy*len-py*w, by-dy*1.0-py*w, by-dy*1.0+py*w];
        set(hRect, 'XData', turretX, 'YData', turretY);

        % Small glowing emitter at barrel tip.
        th = linspace(0, 2*pi, 36);
        tipX = bx + dx*len;
        tipY = by + dy*len;
        set(hBall, 'XData', tipX + 1.25*cos(th), 'YData', tipY + 1.25*sin(th));

        % Laser beam: update both glow and core layers.
        set(hBeamGlow, 'XData', [tipX, tipX+dx*120], 'YData', [tipY, tipY+dy*120]);
        set(hBeam,     'XData', [tipX, tipX+dx*120], 'YData', [tipY, tipY+dy*120]);

        if (epochRunning || inAlignCountdown || (preEpochHold && currentShowLaser)) && currentShowLaser
            set(hBeam,     'Visible', 'on');
            set(hBeamGlow, 'Visible', 'on');
        else
            set(hBeam,     'Visible', 'off');
            set(hBeamGlow, 'Visible', 'off');
        end
    end

    function updatePlanetForCurrentEpoch()
        % The asteroid endpoint should visually terminate at the planet.
        % Because the vertical trajectory is a triangular wave, the endpoint
        % can depend on waveFreq. Compute the y-coordinate at t=1 and move
        % the planet there before drawing the path or starting the epoch.
        osc_amp = 30;
        endY = AST_SPAWN_Y + osc_amp * -(2/pi) * asin(sin(2 * pi * currentWaveFreq));

        planetRadius = 7;
        currentEarthX = EARTH_X;
        currentEarthY = min(max(endY, YL(1) + planetRadius + 1), YL(2) - planetRadius - 1);
        updateEarthGraphic(hEarth, currentEarthX, currentEarthY);
    end

    function doDeflectionFlash()
        th = linspace(0, 2*pi, 80);
        for r = 1:3
            if ~isvalid(fig)
                return;
            end
            set(hFlash, 'XData', currentEarthX + (7 + r*4)*cos(th), ...
                'YData', currentEarthY + (7 + r*4)*sin(th), ...
                'FaceAlpha', 0.12*(4-r));
            drawnow;
            pause(0.08);
        end
        set(hFlash, 'XData', NaN, 'YData', NaN, 'FaceAlpha', 0);
    end

%% ========================================================================
%  Close
% ========================================================================
    function onClose(~,~)
        stopAnim();
        disconnectSerial();
        disconnectLumo();
        try
            delete(fig);
        catch
        end
    end

end

%% =========================================================================
%  File-level helper functions
% =========================================================================
function createDefaultConfig(filename, defaultTrialDur, defaultRestDur, defaultThresh)
% Creates a balanced 40-epoch pilot config:
% 5 speeds x 2 conditions x 4 repeats. Rest durations are pseudorandom
% and average exactly 6 seconds across the full task.

speeds = [0.5 1.5 2.5 3.0 3.5];
showLaserVals = [false true];
restVals = [4 5 5 6 6 7 7 8]; % mean = 6 across each 8-epoch block

rows = {};
k = 0;
for rep = 1:4
    for s = 1:numel(speeds)
        for c = 1:numel(showLaserVals)
            k = k + 1;
            restDur = restVals(mod(k-1, numel(restVals)) + 1);
            rows(end+1, :) = {k, speeds(s), showLaserVals(c), restDur, defaultTrialDur, defaultThresh, false}; %#ok<AGROW>
        end
    end
end

% Fixed pseudorandom order for pilot testing while preserving 4 tracking and
% 4 observation epochs per speed.
rng(7);
order = randperm(size(rows, 1));
rows = rows(order, :);
for i = 1:size(rows, 1)
    rows{i, 1} = i;
end

T = cell2table(rows, 'VariableNames', ...
    {'epoch','waveFreq','SHOW_LASER','REST_DURATION','TRIAL_DURATION','TRACKING_THRESHOLD','SHOW_ASTEROID_PATH'});
writetable(T, filename);
end

function cfg = loadEpochConfig(filename, defaultTrialDur, defaultRestDur, defaultThresh, defaultShowPath)
% Robust config reader. Supports CSV files with headers.

cfg = readtable(filename);

required = {'epoch','waveFreq','SHOW_LASER','REST_DURATION'};
for i = 1:numel(required)
    if ~ismember(required{i}, cfg.Properties.VariableNames)
        error('Config file is missing required column: %s', required{i});
    end
end

cfg.epoch = double(cfg.epoch);
cfg.waveFreq = double(cfg.waveFreq);
cfg.SHOW_LASER = parseBoolColumn(cfg.SHOW_LASER);
cfg.REST_DURATION = double(cfg.REST_DURATION);

if ~ismember('TRIAL_DURATION', cfg.Properties.VariableNames)
    cfg.TRIAL_DURATION = repmat(defaultTrialDur, height(cfg), 1);
else
    cfg.TRIAL_DURATION = double(cfg.TRIAL_DURATION);
end

if ~ismember('TRACKING_THRESHOLD', cfg.Properties.VariableNames)
    cfg.TRACKING_THRESHOLD = repmat(defaultThresh, height(cfg), 1);
else
    cfg.TRACKING_THRESHOLD = double(cfg.TRACKING_THRESHOLD);
end

if ~ismember('SHOW_ASTEROID_PATH', cfg.Properties.VariableNames)
    cfg.SHOW_ASTEROID_PATH = repmat(defaultShowPath, height(cfg), 1);
else
    cfg.SHOW_ASTEROID_PATH = parseBoolColumn(cfg.SHOW_ASTEROID_PATH);
end

% Basic validation
if any(isnan(cfg.waveFreq)) || any(cfg.waveFreq <= 0)
    error('All waveFreq values must be positive numbers.');
end
if any(isnan(cfg.TRIAL_DURATION)) || any(cfg.TRIAL_DURATION <= 0)
    error('All TRIAL_DURATION values must be positive numbers.');
end
if any(isnan(cfg.REST_DURATION)) || any(cfg.REST_DURATION < 0)
    error('All REST_DURATION values must be non-negative numbers.');
end
if any(isnan(cfg.TRACKING_THRESHOLD)) || any(cfg.TRACKING_THRESHOLD < 0) || any(cfg.TRACKING_THRESHOLD > 1)
    error('TRACKING_THRESHOLD values must be between 0 and 1.');
end
end

function out = parseBoolColumn(x)
if islogical(x)
    out = x;
    return;
end

if isnumeric(x)
    out = x ~= 0;
    return;
end

if iscell(x)
    out = false(numel(x), 1);
    for i = 1:numel(x)
        out(i) = parseSingleBool(x{i});
    end
    return;
end

if isstring(x)
    out = false(numel(x), 1);
    for i = 1:numel(x)
        out(i) = parseSingleBool(char(x(i)));
    end
    return;
end

if ischar(x)
    out = parseSingleBool(x);
    return;
end

error('Could not parse boolean config column.');
end

function tf = parseSingleBool(v)
if islogical(v)
    tf = v;
    return;
end
if isnumeric(v)
    tf = v ~= 0;
    return;
end

s = lower(strtrim(char(v)));
tf = any(strcmp(s, {'true','t','yes','y','1','tracking','laser'}));
end

function [ok, msg] = ensureFolder(folderPath)
    ok = true;
    msg = '';
    try
        if ~exist(folderPath, 'dir')
            [status, mkdirMsg] = mkdir(folderPath);
            if ~status
                ok = false;
                msg = sprintf('mkdir failed for %s: %s', folderPath, mkdirMsg);
                return;
            end
        end
        testFile = [tempname(folderPath) '.tmp'];
        fid = fopen(testFile, 'w');
        if fid == -1
            ok = false;
            msg = sprintf('Folder exists but is not writable: %s', folderPath);
            return;
        end
        fprintf(fid, 'test');
        fclose(fid);
        if exist(testFile, 'file')
            delete(testFile);
        end
    catch ME
        ok = false;
        msg = ME.message;
        try
            if exist('fid', 'var') && fid ~= -1
                fclose(fid);
            end
        catch
        end
    end
end

% Map dial raw value (0-1023) to planet zone 1/2/3
function z = dialZone(val)
if val >= 683
    z = 1;
elseif val >= 341
    z = 2;
else
    z = 3;
end
end

% -------------------------------------------------------------------------
function h = drawPlanet(ax, cx, cy, planetID)
% Dispatcher – calls the right planet drawing function.
switch planetID
    case 2,  h = drawMars(ax, cx, cy);
    case 3,  h = drawIceGiant(ax, cx, cy);
    otherwise, h = drawEarth(ax, cx, cy);
end
end

% -------------------------------------------------------------------------
function h = drawMars(ax, cx, cy)
% Stylised Mars: rusty red body, tan dust bands, polar ice cap, craters.
h = gobjects(0);
th = linspace(0, 2*pi, 160);
r = 7;

    function addPatch(x0, y0, colorVal, varargin)
        hp = fill(ax, cx+x0, cy+y0, colorVal, varargin{:});
        set(hp, 'UserData', struct('x0', x0, 'y0', y0));
        h(end+1) = hp; 
    end

% Atmosphere glow – faint amber
for gr = 5:-1:1
    rr = r + gr*0.75;
    addPatch(rr*cos(th), rr*sin(th), [0.9 0.35 0.05], ...
        'EdgeColor', 'none', 'FaceAlpha', 0.018*gr);
end

% Base body – rust orange-red
addPatch(r*cos(th), r*sin(th), [0.75 0.22 0.08], ...
    'EdgeColor', [1.0 0.55 0.25], 'LineWidth', 1.8);

% Night-side shadow
nightX = r * cos(linspace(pi/2, 3*pi/2, 90));
nightY = r * sin(linspace(pi/2, 3*pi/2, 90));
addPatch([nightX fliplr(0.25*r*cos(linspace(3*pi/2, pi/2, 90)))], ...
         [nightY fliplr(r*sin(linspace(3*pi/2, pi/2, 90)))], ...
         [0.12 0.04 0.01], 'EdgeColor', 'none', 'FaceAlpha', 0.28);

% Dust / terrain bands
addPatch([-5.8 -3.5 0.0 3.5 5.8 4.2 0.0 -4.2], ...
         [ 1.2  1.8 1.4 1.8  1.2 0.4 0.8  0.4], ...
         [0.88 0.52 0.18], 'EdgeColor', 'none', 'FaceAlpha', 0.45);
addPatch([-4.8 -2.0 1.0 3.8 4.5 2.0 -1.0 -3.5], ...
         [-0.8 -1.5 -1.2 -0.9 0.0 0.6  0.3 -0.2], ...
         [0.62 0.18 0.06], 'EdgeColor', 'none', 'FaceAlpha', 0.35);

% Polar ice cap (north)
addPatch([-2.2 2.2 1.5 0 -1.5], [5.5 5.5 6.3 6.7 6.3], ...
    [0.95 0.95 1.0], 'EdgeColor', 'none', 'FaceAlpha', 0.85);

% Impact craters
craterData = [-2.5 1.8 0.7; 2.8 -1.0 0.55; -1.0 -2.8 0.65; 3.5 2.5 0.45];
for i = 1:size(craterData,1)
    addPatch(craterData(i,1) + craterData(i,3)*cos(th), ...
             craterData(i,2) + craterData(i,3)*sin(th), ...
             [0.55 0.14 0.04], 'EdgeColor', [0.85 0.45 0.2], ...
             'LineWidth', 0.5, 'FaceAlpha', 0.7);
end

% Specular highlight
addPatch(-2.8 + 2.0*cos(th), 2.6 + 1.1*sin(th), [1 1 1], ...
    'EdgeColor', 'none', 'FaceAlpha', 0.12);
end

% -------------------------------------------------------------------------
function h = drawIceGiant(ax, cx, cy)
% Stylised Ice Giant (Neptune-like): deep blue-teal body, bright band, storm spot.
h = gobjects(0);
th = linspace(0, 2*pi, 160);
r = 7;

    function addPatch(x0, y0, colorVal, varargin)
        hp = fill(ax, cx+x0, cy+y0, colorVal, varargin{:});
        set(hp, 'UserData', struct('x0', x0, 'y0', y0));
        h(end+1) = hp; 
    end

% Atmosphere glow – icy cyan
for gr = 5:-1:1
    rr = r + gr*0.75;
    addPatch(rr*cos(th), rr*sin(th), [0.05 0.6 0.9], ...
        'EdgeColor', 'none', 'FaceAlpha', 0.022*gr);
end

% Base body – deep blue
addPatch(r*cos(th), r*sin(th), [0.04 0.14 0.55], ...
    'EdgeColor', [0.25 0.7 1.0], 'LineWidth', 1.8);

% Night-side shadow
nightX = r * cos(linspace(pi/2, 3*pi/2, 90));
nightY = r * sin(linspace(pi/2, 3*pi/2, 90));
addPatch([nightX fliplr(0.25*r*cos(linspace(3*pi/2, pi/2, 90)))], ...
         [nightY fliplr(r*sin(linspace(3*pi/2, pi/2, 90)))], ...
         [0.01 0.03 0.15], 'EdgeColor', 'none', 'FaceAlpha', 0.30);

% Atmospheric bands
addPatch([-6.2 6.2 5.8 -5.8], [1.8 1.8 0.6 0.6], ...
    [0.08 0.32 0.75], 'EdgeColor', 'none', 'FaceAlpha', 0.50);
addPatch([-5.5 5.5 5.0 -5.0], [-1.0 -1.0 -2.4 -2.4], ...
    [0.12 0.45 0.82], 'EdgeColor', 'none', 'FaceAlpha', 0.40);
addPatch([-6.0 6.0 5.5 -5.5], [3.2 3.2 4.2 4.2], ...
    [0.06 0.25 0.68], 'EdgeColor', 'none', 'FaceAlpha', 0.35);

% Bright equatorial highlight band
addPatch([-6.5 6.5 6.0 -6.0], [-0.2 -0.2 0.5 0.5], ...
    [0.45 0.85 1.0], 'EdgeColor', 'none', 'FaceAlpha', 0.22);

% Storm spot (Great Dark Spot equivalent)
stX = 2.5 + 1.4*cos(th);
stY = -1.8 + 0.85*sin(th);
addPatch(stX, stY, [0.02 0.06 0.25], 'EdgeColor', [0.3 0.7 1], ...
    'LineWidth', 0.8, 'FaceAlpha', 0.85);

% Rings (two arcs, stored in h so updateEarthGraphic can reposition them)
ringAngles = linspace(0.18*pi, 0.82*pi, 60);
rx = 10.5*cos(ringAngles); ry_base = 3.2*sin(ringAngles) - 1.5;
for side = [1 -1]
    for lw = [4.5 1.5]
        col = [0.4 0.7 1.0 0.35];
        if lw < 2; col = [0.7 0.9 1.0 0.20]; end
        hp = plot(ax, cx + side*rx, cy + ry_base, '-', 'Color', col, 'LineWidth', lw);
        set(hp, 'UserData', struct('x0', side*rx, 'y0', ry_base));
        h(end+1) = hp; %#ok<AGROW>
    end
end

% Specular highlight
addPatch(-2.6 + 2.0*cos(th), 2.5 + 1.1*sin(th), [1 1 1], ...
    'EdgeColor', 'none', 'FaceAlpha', 0.15);
end

function drawStars(ax)
rng(42);
scatter(ax, rand(1,250)*100, rand(1,250)*65, rand(1,250)*3+0.5, ...
    'w', 'filled', 'MarkerFaceAlpha', 0.55);
end

function h = drawEarth(ax, cx, cy)
% Draw a stylized Earth with layered atmosphere, continents, cloud bands, and highlight.
% The returned handles store local coordinates in UserData so the planet can be moved
% each epoch without redrawing or changing the graphics stack.

h = gobjects(0);
th = linspace(0, 2*pi, 160);
r = 7;

    function addPatch(x0, y0, colorVal, varargin)
        hp = fill(ax, cx+x0, cy+y0, colorVal, varargin{:});
        set(hp, 'UserData', struct('x0', x0, 'y0', y0));
        h(end+1) = hp; 
    end

% Soft atmosphere glow, drawn first.
for gr = 5:-1:1
    rr = r + gr*0.75;
    addPatch(rr*cos(th), rr*sin(th), [0.15 0.55 1.0], ...
        'EdgeColor', 'none', 'FaceAlpha', 0.025*gr);
end

% Ocean and limb.
addPatch(r*cos(th), r*sin(th), [0.03 0.24 0.68], ...
    'EdgeColor', [0.35 0.75 1.0], 'LineWidth', 1.8);

% Subtle darker night-side overlay.
nightX = r * cos(linspace(pi/2, 3*pi/2, 90));
nightY = r * sin(linspace(pi/2, 3*pi/2, 90));
addPatch([nightX fliplr(0.25*r*cos(linspace(3*pi/2, pi/2, 90)))], ...
         [nightY fliplr(r*sin(linspace(3*pi/2, pi/2, 90)))], ...
         [0.01 0.06 0.18], 'EdgeColor', 'none', 'FaceAlpha', 0.22);

% Stylized continents.
landColor = [0.08 0.62 0.28];
landEdge = [0.35 0.9 0.45];
addPatch([-4.8 -3.8 -2.5 -1.3 -0.4 -1.2 -2.6 -4.1 -5.4 -5.2], ...
         [ 2.4  3.8  3.3  4.1  2.5  1.2  0.7 -0.2  0.5  1.8], ...
         landColor, 'EdgeColor', landEdge, 'LineWidth', 0.5, 'FaceAlpha', 0.95);
addPatch([0.5 1.9 3.4 4.9 5.4 4.2 2.8 1.5 0.3 -0.5], ...
         [2.8 4.2 3.6 2.4 0.8 -0.6 -0.2 -1.2 0.4 1.8], ...
         landColor, 'EdgeColor', landEdge, 'LineWidth', 0.5, 'FaceAlpha', 0.95);
addPatch([-0.8 0.2 1.2 1.5 0.6 -0.6 -1.2], ...
         [-1.6 -2.7 -3.7 -5.1 -5.8 -4.4 -2.8], ...
         [0.07 0.50 0.24], 'EdgeColor', landEdge, 'LineWidth', 0.5, 'FaceAlpha', 0.95);

% Cloud bands.
cloudColor = [0.92 0.97 1.0];
addPatch([-5.5 -4.1 -2.0 0.2 2.1 4.2 5.3], [1.1 1.5 1.2 1.6 1.2 1.4 1.0], ...
    cloudColor, 'EdgeColor', 'none', 'FaceAlpha', 0.42);
addPatch([-4.5 -2.6 -0.5 1.3 3.2 4.8], [-2.2 -1.8 -2.2 -1.7 -2.0 -1.6], ...
    cloudColor, 'EdgeColor', 'none', 'FaceAlpha', 0.34);

% Specular highlight.
highlightX = -2.6 + 2.1*cos(th);
highlightY =  2.5 + 1.2*sin(th);
addPatch(highlightX, highlightY, [1 1 1], 'EdgeColor', 'none', 'FaceAlpha', 0.16);
end

function updateEarthGraphic(h, cx, cy)
for i = 1:numel(h)
    if isgraphics(h(i))
        ud = get(h(i), 'UserData');
        if isstruct(ud) && isfield(ud, 'x0') && isfield(ud, 'y0')
            set(h(i), 'XData', cx + ud.x0, 'YData', cy + ud.y0);
        end
    end
end
end

function [hRect, hBall, hArrow1, hArrow2, hBeam, hBeamGlow, hGun] = drawLauncher(ax, bx, by, ang)
% Moon-based laser platform. The first four outputs are kept compatible with
% the nested updateLauncherGraphic function: hRect is the rotating laser barrel,
% hBall is the glowing emitter, and hArrow1/hArrow2 are decorative aiming marks.

th = linspace(0, 2*pi, 120);
moonR = 5.2;

% Lunar body.
fill(ax, bx+moonR*cos(th), by+moonR*sin(th), [0.72 0.72 0.76], ...
    'EdgeColor', [0.95 0.95 1.0], 'LineWidth', 1.4);
for k = 1:4
    rr = moonR + k*0.35;
    fill(ax, bx+rr*cos(th), by+rr*sin(th), [0.8 0.8 0.9], ...
        'EdgeColor', 'none', 'FaceAlpha', 0.03*(5-k));
end

% Craters — positions rotated 90° clockwise: (x,y) -> (y,-x)
craters = [1.4 2.1 0.8; 1.8 -1.7 0.6; -1.4 -1.1 0.9; -1.6 2.6 0.55; -0.1 0 0.45];
for i = 1:size(craters,1)
    cx = bx + craters(i,1);
    cy = by + craters(i,2);
    cr = craters(i,3);
    fill(ax, cx+cr*cos(th), cy+cr*sin(th), [0.48 0.48 0.52], ...
        'EdgeColor', [0.82 0.82 0.86], 'LineWidth', 0.4, 'FaceAlpha', 0.72);
end

% Mount pad — rotated 90° clockwise so it sits on the right side of the moon.
fill(ax, bx + [-4.2 -4.2 -5.2 -5.2], by + [-3.2 3.2 2.3 -2.3], ...
    [0.42 0.42 0.48], 'EdgeColor', [0.75 0.75 0.82], 'LineWidth', 0.8);

% Initial rotating barrel and emitter.
rad = deg2rad(ang);
dx = cos(rad);
dy = sin(rad);
px = -sin(rad);
py = cos(rad);
len = 8;
w = 1.2;
barrelX = [bx+dx*len+px*w, bx+dx*len-px*w, bx-dx*1.0-px*w, bx-dx*1.0+px*w];
barrelY = [by+dy*len+py*w, by+dy*len-py*w, by-dy*1.0-py*w, by-dy*1.0+py*w];
hRect = fill(ax, barrelX, barrelY, [0.95 0.82 0.18], ...
    'EdgeColor', [1.0 0.95 0.45], 'LineWidth', 1.2);

tipX = bx + dx*len;
tipY = by + dy*len;
hBall = fill(ax, tipX+1.25*cos(th), tipY+1.25*sin(th), [1.0 0.95 0.25], ...
    'EdgeColor', [1 1 0.75], 'LineWidth', 0.8, 'FaceAlpha', 0.95);

% Decorative aiming arcs removed per design update.
hArrow1 = plot(ax, NaN, NaN, '-', 'Color', [0.85 0.85 1.0], 'LineWidth', 1.5, 'Visible', 'off');
hArrow2 = plot(ax, NaN, NaN, '-', 'Color', [0.85 0.85 1.0], 'LineWidth', 1.5, 'Visible', 'off');

% Laser beam: wide soft glow layer + thin bright core for a laser-pointer look.
hBeamGlow = plot(ax, [tipX, tipX+dx*120], [tipY, tipY+dy*120], ...
    '-', 'Color', [1 0.35 0.35 0.18], 'LineWidth', 7.0, 'Visible', 'off');
hBeam = plot(ax, [tipX, tipX+dx*120], [tipY, tipY+dy*120], ...
    '-', 'Color', [1 0.55 0.55], 'LineWidth', 1.2, 'Visible', 'off');

% Gun components (barrel + emitter) — hidden during observation epochs.
hGun = [hRect, hBall];
end

% =========================================================================
%  showSubjectIDDialog  —  standalone popup to collect Subject ID
%  Returns the trimmed ID string, or '' if the user closed the window.
% =========================================================================
function id = showSubjectIDDialog()

id = '';   % default: cancelled

dlgW = 420;  dlgH = 200;
scrn = get(0, 'ScreenSize');
dlgX = round((scrn(3) - dlgW) / 2);
dlgY = round((scrn(4) - dlgH) / 2);

dlg = figure( ...
    'Name',        'Lunar Blast — Subject ID', ...
    'Color',       [0.06 0.06 0.10], ...
    'NumberTitle', 'off', ...
    'MenuBar',     'none', ...
    'ToolBar',     'none', ...
    'Resize',      'off', ...
    'Position',    [dlgX dlgY dlgW dlgH], ...
    'WindowStyle', 'modal', ...
    'CloseRequestFcn', @onCancel);

% Title label
uicontrol('Parent', dlg, 'Style', 'text', ...
    'String',          'LUNAR BLAST', ...
    'ForegroundColor', [0.75 0.80 1.00], ...
    'BackgroundColor', [0.06 0.06 0.10], ...
    'FontName',        'Courier New', ...
    'FontSize',        16, ...
    'FontWeight',      'bold', ...
    'HorizontalAlignment', 'center', ...
    'Units', 'pixels', 'Position', [20 150 380 30]);

% Prompt label
uicontrol('Parent', dlg, 'Style', 'text', ...
    'String',          'Enter Subject ID:', ...
    'ForegroundColor', [0.70 0.75 0.95], ...
    'BackgroundColor', [0.06 0.06 0.10], ...
    'FontName',        'Courier New', ...
    'FontSize',        11, ...
    'HorizontalAlignment', 'center', ...
    'Units', 'pixels', 'Position', [20 112 380 24]);

% Edit box
hEdit = uicontrol('Parent', dlg, 'Style', 'edit', ...
    'String',          'TEST001', ...
    'BackgroundColor', [0.10 0.10 0.16], ...
    'ForegroundColor', [0.92 0.96 1.00], ...
    'FontName',        'Courier New', ...
    'FontSize',        13, ...
    'HorizontalAlignment', 'center', ...
    'Units', 'pixels', 'Position', [80 76 260 32], ...
    'KeyPressFcn',     @onKeyPress);

% OK button
uicontrol('Parent', dlg, 'Style', 'pushbutton', ...
    'String',          'OK', ...
    'BackgroundColor', [0.15 0.38 0.72], ...
    'ForegroundColor', [1.00 1.00 1.00], ...
    'FontName',        'Courier New', ...
    'FontSize',        12, ...
    'FontWeight',      'bold', ...
    'Units', 'pixels', 'Position', [140 22 140 36], ...
    'Callback',        @onOK);

uicontrol(hEdit);   % put focus in the text box

uiwait(dlg);        % block until onOK or onCancel closes the dialog

    % ---- callbacks ----
    function onOK(~,~)
        raw = strtrim(get(hEdit, 'String'));
        if isempty(raw)
            raw = 'TEST001';
        end
        id = raw;
        if isvalid(dlg)
            delete(dlg);
        end
    end

    function onCancel(~,~)
        id = '';
        if isvalid(dlg)
            delete(dlg);
        end
    end

    function onKeyPress(~, evt)
        if strcmp(evt.Key, 'return')
            onOK();
        end
    end

end
