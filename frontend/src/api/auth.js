import { api } from './client.js'

export function login(email, password) {
  return api.post('/auth/login', { email, password })
}

export function register(email, password) {
  return api.post('/auth/register', { email, password })
}

export function me() {
  return api.get('/auth/me')
}
