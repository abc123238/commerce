import { api } from './client.js'

export function listProducts(params = {}) {
  const qs = new URLSearchParams()
  if (params.page) qs.set('page', params.page)
  if (params.size) qs.set('size', params.size)
  if (params.category_id) qs.set('category_id', params.category_id)
  if (params.keyword) qs.set('keyword', params.keyword)
  const query = qs.toString()
  return api.get(`/products${query ? '?' + query : ''}`)
}

export function listCategories() {
  return api.get('/categories')
}
