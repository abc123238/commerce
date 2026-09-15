import { api } from './client.js'

export function getCart() {
  return api.get('/cart')
}

export function addToCart(productId, quantity = 1) {
  return api.post('/cart/items', { product_id: productId, quantity })
}

export function updateCartItem(productId, quantity) {
  return api.patch(`/cart/items/${productId}`, { quantity })
}

export function removeCartItem(productId) {
  return api.del(`/cart/items/${productId}`)
}
