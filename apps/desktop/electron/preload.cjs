const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('herDesktop', {
  getConnection: profile => ipcRenderer.invoke('her:connection', profile),
  touchBackend: profile => ipcRenderer.invoke('her:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('her:gateway:ws-url', profile),
  getBootProgress: () => ipcRenderer.invoke('her:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('her:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('her:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('her:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('her:connection-config:test', payload),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('her:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('her:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('her:connection-config:oauth-logout', remoteUrl),
  profile: {
    get: () => ipcRenderer.invoke('her:profile:get'),
    set: name => ipcRenderer.invoke('her:profile:set', name)
  },
  api: request => ipcRenderer.invoke('her:api', request),
  notify: payload => ipcRenderer.invoke('her:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('her:requestMicrophoneAccess'),
  readFileDataUrl: filePath => ipcRenderer.invoke('her:readFileDataUrl', filePath),
  readFileText: filePath => ipcRenderer.invoke('her:readFileText', filePath),
  selectPaths: options => ipcRenderer.invoke('her:selectPaths', options),
  writeClipboard: text => ipcRenderer.invoke('her:writeClipboard', text),
  saveImageFromUrl: url => ipcRenderer.invoke('her:saveImageFromUrl', url),
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('her:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('her:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('her:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('her:watchPreviewFile', url),
  stopPreviewFileWatch: id => ipcRenderer.invoke('her:stopPreviewFileWatch', id),
  setTitleBarTheme: payload => ipcRenderer.send('her:titlebar-theme', payload),
  setPreviewShortcutActive: active => ipcRenderer.send('her:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('her:openExternal', url),
  fetchLinkTitle: url => ipcRenderer.invoke('her:fetchLinkTitle', url),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('her:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('her:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('her:setting:defaultProjectDir:pick')
  },
  revealLogs: () => ipcRenderer.invoke('her:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('her:logs:recent'),
  readDir: dirPath => ipcRenderer.invoke('her:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('her:fs:gitRoot', startPath),
  terminal: {
    dispose: id => ipcRenderer.invoke('her:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('her:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('her:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('her:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `her:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)
      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `her:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)
      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('her:close-preview-requested', listener)
    return () => ipcRenderer.removeListener('her:close-preview-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('her:open-updates', listener)
    return () => ipcRenderer.removeListener('her:open-updates', listener)
  },
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('her:window-state-changed', listener)
    return () => ipcRenderer.removeListener('her:window-state-changed', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('her:preview-file-changed', listener)
    return () => ipcRenderer.removeListener('her:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('her:backend-exit', listener)
    return () => ipcRenderer.removeListener('her:backend-exit', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('her:power-resume', listener)
    return () => ipcRenderer.removeListener('her:power-resume', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('her:boot-progress', listener)
    return () => ipcRenderer.removeListener('her:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.cjs (apps/desktop/electron/bootstrap-runner.cjs).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('her:bootstrap:get'),
  resetBootstrap: () => ipcRenderer.invoke('her:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('her:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('her:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('her:bootstrap:event', listener)
    return () => ipcRenderer.removeListener('her:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('her:version'),
  uninstall: {
    summary: () => ipcRenderer.invoke('her:uninstall:summary'),
    run: mode => ipcRenderer.invoke('her:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('her:updates:check'),
    apply: opts => ipcRenderer.invoke('her:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('her:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('her:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('her:updates:progress', listener)
      return () => ipcRenderer.removeListener('her:updates:progress', listener)
    }
  }
})
