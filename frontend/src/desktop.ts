declare global {
  interface Window {
    pywebview?: { api: { save_file(url: string, filename: string): Promise<{ saved?: boolean; error?: string }> } }
  }
}

export async function saveDesktopFile(url: string, filename: string): Promise<boolean | null> {
  if (!window.pywebview) return null
  const result = await window.pywebview.api.save_file(url, filename)
  if (result.error) throw new Error(result.error)
  return !!result.saved
}
