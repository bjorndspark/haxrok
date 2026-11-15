import type { ProjectFile } from "./types"

/** Only these leave the user's machine: the PCB, backups and 3D models stay. */
const SENT_SUFFIXES = [".kicad_pro", ".kicad_sch"]

/** The last review's findings, saved next to the `.kicad_pro` (plan 011). */
export const REVIEW_SUFFIX = ".review.json"

export function isSchematicFile(path: string): boolean {
  return SENT_SUFFIXES.some((s) => path.toLowerCase().endsWith(s))
}

/** Schematic files, plus any review file until `sentFiles` picks the project's. */
function isWanted(path: string): boolean {
  return isSchematicFile(path) || path.endsWith(REVIEW_SUFFIX)
}

/** The project's review file: `<name>.review.json` beside its `.kicad_pro`. */
export function reviewFilePath(files: ProjectFile[]): string | null {
  const pro = files.find((f) => f.path.endsWith(".kicad_pro"))
  return pro ? pro.path.replace(/\.kicad_pro$/, REVIEW_SUFFIX) : null
}

/** Folders KiCad fills with copies of the schematic; never the live project. */
function isBackupPath(path: string): boolean {
  return path.split("/").some((part) => part.endsWith("-backups") || part === ".history")
}

/** The live project's schematic files and its review file; other review
 *  files (another project's, a renamed copy) stay on the machine. */
export function sentFiles(files: ProjectFile[]): ProjectFile[] {
  const live = files.filter((f) => isWanted(f.path) && !isBackupPath(f.path))
  const review = reviewFilePath(live)
  return live
    .filter((f) => !f.path.endsWith(REVIEW_SUFFIX) || f.path === review)
    .sort((a, b) => a.path.localeCompare(b.path))
}

/** Files from an `<input webkitdirectory>` or plain multi-file picker. */
export function fromFileList(list: FileList): ProjectFile[] {
  return sentFiles(
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
    if (isWanted(path)) out.push({ path, file: await entryFile(entry as FileSystemFileEntry) })
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
  return sentFiles(out)
}

/** The project's display name: the .kicad_pro's name, else the lone sheet's. */
export function projectName(files: ProjectFile[]): string | null {
  const base = (p: string) => (p.split("/").pop() ?? p).replace(/\.kicad_(pro|sch)$/, "")
  const pro = files.find((f) => f.path.endsWith(".kicad_pro"))
  if (pro) return base(pro.path)
  return files.length > 0 ? base(files[0].path) : null
}
