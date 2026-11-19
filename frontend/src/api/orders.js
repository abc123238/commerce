import { api } from './client.js'

export function createOrder() {
  return api.post('/orders')
}

export function listOrders() {
  return api.get('/orders')
}

export function getOrder(orderNo) {
  return api.get(`/orders/${orderNo}`)
}

export function payOrder(orderNo) {
  return api.post(`/orders/${orderNo}/pay`)
}

export function cancelOrder(orderNo) {
  return api.post(`/orders/${orderNo}/cancel`)
}

export function getTracking(orderNo) {
  return api.get(`/orders/${orderNo}/tracking`)
}
