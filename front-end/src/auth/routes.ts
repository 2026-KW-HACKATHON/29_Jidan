/** Backend callback routes are product entries in every build, including dev previews. */
export const callbackPaths: Record<string, '/signup' | '/home'> = { '/__auth/signup': '/signup', '/__auth/session': '/home' }
export const canonicalAuthPath = (path: string) => Object.hasOwn(callbackPaths, path) ? callbackPaths[path] : path
export const isPreviewPath = (path: string) => path.startsWith('/__') && !Object.hasOwn(callbackPaths, path)
