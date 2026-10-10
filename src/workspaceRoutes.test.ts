import { expect, it } from 'vitest'
import { parseWorkspaceRoute, workspacePath } from './workspaceRoutes'

it('starts at the platform without selecting a project', () => {
  expect(parseWorkspaceRoute('/')).toEqual({page:'overview', projectId:null, reviewId:null})
})
it('keeps member management exclusively inside project settings', () => {
  expect(parseWorkspaceRoute('/projects/chaos/settings/members')).toEqual({ page: 'members', projectId: 'chaos', reviewId: null })
  expect(parseWorkspaceRoute('/platform/settings/members')).toBeNull()
})
it('separates platform defaults from project settings in stable URLs', () => {
  expect(workspacePath('models', null)).toBe('/platform/settings/models')
  expect(workspacePath('models', 'chaos')).toBe('/projects/chaos/settings/models')
  expect(parseWorkspaceRoute('/projects/chaos/settings/models')).toEqual({page:'models',projectId:'chaos',reviewId:null})
})
it('retains the review and its owning project on reload', () => {
  expect(parseWorkspaceRoute('/projects/chaos/reviews/r1')).toEqual({page:'review-detail',projectId:'chaos',reviewId:'r1'})
})
it('does not treat unknown routes or project-only settings as the platform overview', () => {
  expect(parseWorkspaceRoute('/platform/settings/policy')).toBeNull()
  expect(parseWorkspaceRoute('/projects/chaos/settings/system')).toBeNull()
  expect(parseWorkspaceRoute('/projects/%ZZ/reviews')).toBeNull()
})
