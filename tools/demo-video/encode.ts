/**
 * Сборка результата: webm от Playwright → mp4 (H.264) с главами, плюс demo.srt и chapters.txt.
 * Можно запустить отдельно: OUT=out npm run encode (берёт OUT/timeline.json и OUT/raw.webm).
 */
import { execFileSync, spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export interface Cue {
  start: number
  end: number
  text: string
}

export interface Chapter {
  start: number
  title: string
}

export interface Timeline {
  duration: number
  cues: Cue[]
  chapters: Chapter[]
}

function pad(n: number, size = 2) {
  return String(Math.floor(n)).padStart(size, '0')
}

export function srtTime(ms: number): string {
  const t = Math.max(0, Math.round(ms))
  return `${pad(t / 3_600_000)}:${pad((t / 60_000) % 60)}:${pad((t / 1000) % 60)},${pad(t % 1000, 3)}`
}

export function chapterTime(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`
}

export function toSrt(cues: Cue[]): string {
  return cues
    .filter((c) => c.text && c.end > c.start)
    .map((c, i) => `${i + 1}\n${srtTime(c.start)} --> ${srtTime(c.end)}\n${wrap(c.text)}\n`)
    .join('\n')
}

/** Строки субтитров не длиннее ~60 символов, иначе плееры режут их как попало. */
function wrap(text: string, max = 60): string {
  const words = text.split(/\s+/)
  const lines: string[] = []
  let line = ''
  for (const word of words) {
    if (line && (line + ' ' + word).length > max) {
      lines.push(line)
      line = word
    } else {
      line = line ? `${line} ${word}` : word
    }
  }
  if (line) lines.push(line)
  return lines.join('\n')
}

export function toChapters(chapters: Chapter[]): string {
  // YouTube требует первую главу с 00:00
  const list = [...chapters].sort((a, b) => a.start - b.start)
  if (list[0]) list[0] = { ...list[0], start: 0 }
  return list.map((c) => `${chapterTime(c.start)} ${c.title}`).join('\n') + '\n'
}

function ffmetadata(t: Timeline): string {
  const list = [...t.chapters].sort((a, b) => a.start - b.start)
  const escape = (s: string) => s.replace(/([=;#\\\n])/g, '\\$1')
  let out = ';FFMETADATA1\ntitle=ChurnRadar — демо\n'
  list.forEach((c, i) => {
    const start = i === 0 ? 0 : Math.round(c.start)
    const end = Math.round(list[i + 1]?.start ?? t.duration)
    if (end <= start) return
    out += `\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=${start}\nEND=${end}\ntitle=${escape(c.title)}\n`
  })
  return out
}

function hasLocalFfmpeg(): boolean {
  if (process.env.FFMPEG_DOCKER === '1') return false
  const r = spawnSync(process.env.FFMPEG ?? 'ffmpeg', ['-version'], { stdio: 'ignore' })
  return r.status === 0
}

function ffmpeg(args: string[], dir: string) {
  if (hasLocalFfmpeg()) {
    execFileSync(process.env.FFMPEG ?? 'ffmpeg', args, { cwd: dir, stdio: ['ignore', 'ignore', 'inherit'] })
    return
  }
  // запасной путь: ffmpeg из docker-образа, папка с файлами монтируется в /work
  const image = process.env.FFMPEG_IMAGE ?? 'jrottenberg/ffmpeg:6.1-ubuntu'
  console.log(`[encode] локального ffmpeg нет, беру ${image}`)
  execFileSync('docker', ['run', '--rm', '-v', `${dir}:/work`, '-w', '/work', image, ...args], {
    stdio: ['ignore', 'ignore', 'inherit'],
  })
}

export function encode(outDir: string, webm: string, t: Timeline) {
  const dir = resolve(outDir)
  writeFileSync(resolve(dir, 'demo.srt'), toSrt(t.cues), 'utf-8')
  writeFileSync(resolve(dir, 'chapters.txt'), toChapters(t.chapters), 'utf-8')
  writeFileSync(resolve(dir, 'chapters.ffmeta'), ffmetadata(t), 'utf-8')
  // файлы передаём относительными путями: так одинаково работает и локальный ffmpeg, и docker
  ffmpeg(
    [
      '-y', '-loglevel', 'error',
      '-i', basename(webm),
      '-i', 'chapters.ffmeta',
      '-map', '0:v:0', '-map_metadata', '1', '-map_chapters', '1',
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
      '-pix_fmt', 'yuv420p', '-r', '25', '-movflags', '+faststart',
      'demo.mp4',
    ],
    dir,
  )
  console.log(`[encode] готово: ${resolve(dir, 'demo.mp4')}`)
}

// запуск напрямую: OUT=out npm run encode
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const out = resolve(process.env.OUT ?? resolve(dirname(fileURLToPath(import.meta.url)), 'out'))
  const timeline = JSON.parse(readFileSync(resolve(out, 'timeline.json'), 'utf-8')) as Timeline
  const webm = resolve(out, 'raw.webm')
  if (!existsSync(webm)) throw new Error(`нет ${webm}`)
  encode(out, webm, timeline)
}
