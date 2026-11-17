import type { ProjectFile } from "./types"

/** Only these leave the user's machine, with the board when the user opts
 *  in (`BOARD_SUFFIX`): backups and 3D models stay. */
const SENT_SUFFIXES = [".kicad_pro", ".kicad_sch"]

/** The last review's findings, saved next to the `.kicad_pro` (plan 011). */
export const REVIEW_SUFFIX = ".review.json"

/** The PCB, sent only when the user ticks "also send the PCB" (plan 013). */
export const BOARD_SUFFIX = ".kicad_pcb"

export function isSchematicFile(path: string): boolean {
  return SENT_SUFFIXES.some((s) => path.toLowerCase().endsWith(s))
}

/** Schematic files, plus any review file or board until `sentFiles` picks
 *  the project's. */
function isWanted(path: string): boolean {
  return isSchematicFile(path) || path.endsWith(REVIEW_SUFFIX) || path.endsWith(BOARD_SUFFIX)
}

/** The project's review file: `<name>.review.json` beside its `.kicad_pro`. */
export function reviewFilePath(files: ProjectFile[]): string | null {
  const pro = files.find((f) => f.path.endsWith(".kicad_pro"))
  return pro ? pro.path.replace(/\.kicad_pro$/, REVIEW_SUFFIX) : null
}

/** The project's boards: `<name>.kicad_pcb` beside its `.kicad_pro`, or
 *  beside a sheet when there is no `.kicad_pro` (the backend keeps the one
 *  named after the root sheet). */
function isProjectBoard(path: string, files: ProjectFile[]): boolean {
  const pro = files.find((f) => f.path.endsWith(".kicad_pro"))
  const roots = pro ? [pro] : files.filter((f) => f.path.endsWith(".kicad_sch"))
  return roots.some((f) => f.path.replace(/\.kicad_(pro|sch)$/, BOARD_SUFFIX) === path)
}

/** Folders KiCad fills with copies of the schematic; never the live project. */
function isBackupPath(path: string): boolean {
  return path.split("/").some((part) => part.endsWith("-backups") || part === ".history")
}

/** The live project's schematic files, its review file and its board; other
 *  review files and boards (another project's, a renamed copy) stay on the
 *  machine. */
export function sentFiles(files: ProjectFile[]): ProjectFile[] {
  const live = files.filter((f) => isWanted(f.path) && !isBackupPath(f.path))
  const review = reviewFilePath(live)
  return live
    .filter((f) => !f.path.endsWith(REVIEW_SUFFIX) || f.path === review)
    .filter((f) => !f.path.endsWith(BOARD_SUFFIX) || isProjectBoard(f.path, live))
    .sort((a, b) => a.path.localeCompare(b.path))
}

/** What one run sends: the review file only in review mode (it is a review's
 *  baseline), the board only when the user ticked "also send the PCB". */
export function runFiles(
  files: ProjectFile[],
  { review, sendBoard }: { review: boolean; sendBoard: boolean },
): ProjectFile[] {
  return files.filter(
    (f) =>
      (review || !f.path.endsWith(REVIEW_SUFFIX)) && (sendBoard || !f.path.endsWith(BOARD_SUFFIX)),
  )
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

/** The project's display name: the .kicad_pro's name, else the first sheet's. */
export function projectName(files: ProjectFile[]): string | null {
  const base = (p: string) => (p.split("/").pop() ?? p).replace(/\.kicad_(pro|sch)$/, "")
  const pro = files.find((f) => f.path.endsWith(".kicad_pro"))
  if (pro) return base(pro.path)
  const sheet = files.find((f) => isSchematicFile(f.path))
  return sheet ? base(sheet.path) : null
}

const SEND_BOARD_KEY = "sendBoard"

/** Whether this browser last ticked "also send the PCB"; off by default. */
export function savedSendBoard(): boolean {
  try {
    return localStorage.getItem(SEND_BOARD_KEY) === "1"
  } catch {
    return false
  }
}

/** Remember the "also send the PCB" choice in this browser. */
export function saveSendBoard(on: boolean): void {
  try {
    localStorage.setItem(SEND_BOARD_KEY, on ? "1" : "0")
  } catch {
    // Storage blocked (private mode): the toggle starts off next time.
  }
}
