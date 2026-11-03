import type { ProjectFile } from "./types"

/** Only these leave the user's machine: the PCB, backups and 3D models stay. */
const SENT_SUFFIXES = [".kicad_pro", ".kicad_sch"]

export function isSchematicFile(path: string): boolean {
  return SENT_SUFFIXES.some((s) => path.toLowerCase().endsWith(s))
}

/** Folders KiCad fills with copies of the schematic; never the live project. */
function isBackupPath(path: string): boolean {
  return path.split("/").some((part) => part.endsWith("-backups") || part === ".history")
}

function keep(files: ProjectFile[]): ProjectFile[] {
  return files
    .filter((f) => isSchematicFile(f.path) && !isBackupPath(f.path))
    .sort((a, b) => a.path.localeCompare(b.path))
}

/** Files from an `<input webkitdirectory>` or plain multi-file picker. */
export function fromFileList(list: FileList): ProjectFile[] {
  return keep(
    Array.from(list, (file) => ({
      path: file.webkitRelativePath || file.name,
      file,
    })),
  )
}

function readEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    reader.readEntries(resolve, reject)
  })
}

function entryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject)
  })
}

async function walk(entry: FileSystemEntry, out: ProjectFile[]): Promise<void> {
  const path = entry.fullPath.replace(/^\//, "")
  if (entry.isFile) {
    if (isSchematicFile(path))
      out.push({ path, file: await entryFile(entry as FileSystemFileEntry) })
    return
  }
  if (!entry.isDirectory || isBackupPath(path)) return
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  // readEntries returns at most ~100 entries per call; read until empty.
  for (;;) {
    const batch = await readEntries(reader)
    if (batch.length === 0) break
    for (const child of batch) await walk(child, out)
  }
}

/** Files from a drop: walks dropped folders recursively. */
export async function fromDataTransfer(dt: DataTransfer): Promise<ProjectFile[]> {
  const entries = Array.from(dt.items)
    .map((item) => item.webkitGetAsEntry())
    .filter((e): e is FileSystemEntry => e !== null)
  if (entries.length === 0) return fromFileList(dt.files)
  const out: ProjectFile[] = []
  for (const entry of entries) await walk(entry, out)
  return keep(out)
}

/** The project's display name: the .kicad_pro's name, else the lone sheet's. */
export function projectName(files: ProjectFile[]): string | null {
  const base = (p: string) => (p.split("/").pop() ?? p).replace(/\.kicad_(pro|sch)$/, "")
  const pro = files.find((f) => f.path.endsWith(".kicad_pro"))
  if (pro) return base(pro.path)
  return files.length > 0 ? base(files[0].path) : null
}
