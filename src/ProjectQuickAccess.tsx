import { useState } from 'react'
import * as Popover from '@radix-ui/react-popover'
import { Star, ArrowRight } from 'lucide-react'
import type { Project } from './types'

const preview = typeof document !== 'undefined' && document.body.classList.contains('implementation-preview')
const FAVORITES_KEY = preview ? 'cw-cr:preview:favorites' : 'cw-cr:favorites'
const RECENTS_KEY = preview ? 'cw-cr:preview:recent-projects' : 'cw-cr:recent-projects'

function readIds(key: string): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []
  } catch { return [] }
}

export function ProjectQuickAccess({ projects, onOpen }: { projects: Project[]; onOpen: (id: string) => void }) {
  const [favorites, setFavorites] = useState(() => readIds(FAVORITES_KEY))
  const [recents, setRecents] = useState(() => readIds(RECENTS_KEY))
  const [manageOpen, setManageOpen] = useState(false)
  const available = new Set(projects.map(project => project.id))
  const favoriteProjects = projects.filter(project => favorites.includes(project.id))
  const recentProjects = recents.filter(id => available.has(id)).map(id => projects.find(project => project.id === id)!).slice(0, 3)
  function toggleFavorite(id: string) {
    const next = favorites.includes(id) ? favorites.filter(item => item !== id) : [...favorites, id]
    setFavorites(next)
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(next))
  }
  function open(id: string) {
    const next = [id, ...recents.filter(item => item !== id)].slice(0, 5)
    setRecents(next)
    localStorage.setItem(RECENTS_KEY, JSON.stringify(next))
    onOpen(id)
  }
  return <section className="project-quick-access" aria-label="快速进入项目">
    <div className="project-quick-heading"><div><h2>常用项目</h2><p>收藏常用项目，快速进入 MR 列表。</p></div>{recentProjects.length > 0 && <div className="project-recent"><strong>最近访问</strong>{recentProjects.map(project => <button key={project.id} onClick={() => open(project.id)}>{project.name}<ArrowRight size={13} /></button>)}</div>}</div>
    {favoriteProjects.length ? <div className="project-favorites">{favoriteProjects.map(project => <button className="project-favorite-card" key={project.id} aria-label={`进入常用项目 ${project.name}`} onClick={() => open(project.id)}><strong>{project.name}</strong><small>{project.path}</small><span>进入 MR 列表 <ArrowRight size={13} /></span></button>)}</div> : <p className="project-favorites-empty">尚未收藏项目。点击下方列表中的星标即可置顶。</p>}
    <div className="project-star-controls"><Popover.Root open={manageOpen} onOpenChange={setManageOpen} modal={false}>
      <Popover.Trigger asChild><button className="project-manage-toggle" type="button">管理常用项目</button></Popover.Trigger>
      <Popover.Portal><Popover.Content className="project-manage-panel" aria-label="收藏项目" sideOffset={6} align="start">{projects.map(project => <button key={project.id} aria-label={`${favorites.includes(project.id) ? '取消常用' : '设为常用'} ${project.name}`} aria-pressed={favorites.includes(project.id)} onClick={() => toggleFavorite(project.id)}><Star size={14} fill={favorites.includes(project.id) ? 'currentColor' : 'none'} />{project.name}</button>)}</Popover.Content></Popover.Portal>
    </Popover.Root></div>
  </section>
}
