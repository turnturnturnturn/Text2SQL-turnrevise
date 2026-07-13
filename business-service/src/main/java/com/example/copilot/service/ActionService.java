package com.example.copilot.service;

import com.example.copilot.api.ActionDtos.*;
import com.example.copilot.domain.*;
import com.example.copilot.repository.*;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;

@Service
public class ActionService {
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Set<String> OPERATOR_ROLES = Set.of("operator", "admin");

    private final PendingActionRepository actions;
    private final CustomerRepository customers;
    private final ProductRepository products;
    private final OrderRepository orders;
    private final OrderItemRepository orderItems;
    private final AuditService audit;
    private final long approvalTtlMinutes;
    private final ObjectMapper canonicalMapper;

    public ActionService(PendingActionRepository actions, CustomerRepository customers,
                         ProductRepository products, OrderRepository orders,
                         OrderItemRepository orderItems, AuditService audit,
                         @Value("${copilot.approval-ttl-minutes}") long approvalTtlMinutes,
                         ObjectMapper objectMapper) {
        this.actions = actions;
        this.customers = customers;
        this.products = products;
        this.orders = orders;
        this.orderItems = orderItems;
        this.audit = audit;
        this.approvalTtlMinutes = approvalTtlMinutes;
        this.canonicalMapper = objectMapper.copy()
                .configure(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY, true)
                .configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);
    }

    @Transactional
    public PreviewResponse preview(UUID userId, String role, PreviewRequest request) {
        requireWriter(role);
        ActionType type;
        try {
            type = ActionType.valueOf(request.actionType());
        } catch (IllegalArgumentException ex) {
            throw badRequest("Unsupported action type");
        }

        PreviewData preview = switch (type) {
            case CREATE_DRAFT_ORDER -> previewCreate(request.payload());
            case UPDATE_ORDER_STATUS -> previewStatus(request.payload());
            case CANCEL_ORDER -> previewCancel(request.payload());
            case SOFT_DELETE_DRAFT -> previewDelete(request.payload(), role);
        };

        String token = randomToken();
        Instant expiresAt = Instant.now().plus(approvalTtlMinutes, ChronoUnit.MINUTES);
        PendingAction action = new PendingAction(
                userId, type, preview.payload(), sha256(canonicalJson(preview.payload())),
                preview.targetVersion(), preview.impact(), sha256(token), expiresAt);
        actions.save(action);
        audit.recordAfterCommit(userId, "ACTION_PREVIEWED", action.getId(), true,
                Map.of("actionType", type.name(), "impact", preview.impact()));
        return new PreviewResponse(action.getId(), token, preview.impact(), expiresAt, "PENDING");
    }

    @Transactional(noRollbackFor = ExpiredApprovalException.class)
    public ActionResponse confirm(UUID actionId, String token, UUID userId, String role) {
        requireWriter(role);
        PendingAction action = loadAndValidate(actionId, token, userId);
        try {
            Map<String, Object> result = execute(action, role);
            action.confirm();
            actions.save(action);
            audit.recordAfterCommit(userId, "ACTION_CONFIRMED", actionId, true, result);
            return new ActionResponse(actionId, "CONFIRMED", "操作已通过事务执行", result);
        } catch (RuntimeException ex) {
            audit.record(userId, "ACTION_FAILED", actionId, false,
                    Map.of("reason", safeMessage(ex), "actionType", action.getActionType().name()));
            throw ex;
        }
    }

    @Transactional(noRollbackFor = ExpiredApprovalException.class)
    public ActionResponse cancel(UUID actionId, String token, UUID userId, String role) {
        requireWriter(role);
        PendingAction action = loadAndValidate(actionId, token, userId);
        action.cancel();
        actions.save(action);
        audit.recordAfterCommit(userId, "ACTION_CANCELLED", actionId, true, Map.of());
        return new ActionResponse(actionId, "CANCELLED", "操作已取消，未修改业务数据", Map.of());
    }

    private PendingAction loadAndValidate(UUID actionId, String token, UUID userId) {
        PendingAction action = actions.findByIdForUpdate(actionId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Action not found"));
        if (!action.getUserId().equals(userId)) throw forbidden("Action belongs to another user");
        if (action.getStatus() != ActionStatus.PENDING) throw conflict("Action is no longer pending");
        if (action.getExpiresAt().isBefore(Instant.now())) {
            action.expire();
            actions.save(action);
            throw new ExpiredApprovalException();
        }
        if (!MessageDigest.isEqual(
                sha256(token).getBytes(StandardCharsets.UTF_8),
                action.getApprovalTokenHash().getBytes(StandardCharsets.UTF_8))) {
            throw forbidden("Invalid approval token");
        }
        String currentPayloadHash = sha256(canonicalJson(action.getNormalizedPayload()));
        if (!MessageDigest.isEqual(
                currentPayloadHash.getBytes(StandardCharsets.UTF_8),
                action.getPayloadHash().getBytes(StandardCharsets.UTF_8))) {
            throw conflict("Action payload failed its integrity check");
        }
        return action;
    }

    private Map<String, Object> execute(PendingAction action, String role) {
        return switch (action.getActionType()) {
            case CREATE_DRAFT_ORDER -> executeCreate(action.getNormalizedPayload());
            case UPDATE_ORDER_STATUS -> executeStatus(action);
            case CANCEL_ORDER -> executeCancel(action);
            case SOFT_DELETE_DRAFT -> executeDelete(action, role);
        };
    }

    private PreviewData previewCreate(Map<String, Object> raw) {
        long customerId = requiredLong(raw, "customerId");
        Customer customer = customers.findById(customerId)
                .orElseThrow(() -> badRequest("Customer not found"));
        Object rawItems = raw.get("items");
        if (!(rawItems instanceof List<?> list) || list.isEmpty() || list.size() > 20) {
            throw badRequest("items must contain between 1 and 20 entries");
        }
        Map<Long, Integer> requestedQuantities = new LinkedHashMap<>();
        for (Object entry : list) {
            if (!(entry instanceof Map<?, ?> item)) throw badRequest("Invalid item payload");
            long productId = requiredLong(item, "productId");
            long rawQuantity = requiredLong(item, "quantity");
            if (rawQuantity < 1 || rawQuantity > 100) throw badRequest("quantity must be 1..100");
            int quantity = (int) rawQuantity;
            int combined = Math.addExact(requestedQuantities.getOrDefault(productId, 0), quantity);
            if (combined > 100) throw badRequest("combined quantity must be 1..100");
            requestedQuantities.put(productId, combined);
        }

        List<Map<String, Object>> items = new ArrayList<>();
        BigDecimal total = BigDecimal.ZERO;
        for (Map.Entry<Long, Integer> requested : requestedQuantities.entrySet()) {
            long productId = requested.getKey();
            int quantity = requested.getValue();
            Product product = products.findById(productId)
                    .filter(value -> !value.isDeleted())
                    .orElseThrow(() -> badRequest("Product not found: " + productId));
            if (product.getStock() < quantity) throw conflict("Insufficient stock for product " + productId);
            total = total.add(product.getPrice().multiply(BigDecimal.valueOf(quantity)));
            items.add(Map.of(
                    "productId", productId,
                    "quantity", quantity,
                    "productVersion", product.getVersion()));
        }
        Map<String, Object> payload = Map.of("customerId", customerId, "items", items);
        Map<String, Object> impact = Map.of(
                "operation", "create draft order", "customer", customer.getName(),
                "itemCount", items.size(), "totalAmount", total);
        return new PreviewData(payload, null, impact);
    }

    private PreviewData previewStatus(Map<String, Object> raw) {
        CommerceOrder order = activeOrder(requiredLong(raw, "orderId"));
        OrderStatus target = requiredStatus(raw, "targetStatus");
        ensureTransition(order.getStatus(), target);
        Map<String, Object> payload = Map.of("orderId", order.getId(), "targetStatus", target.name());
        Map<String, Object> impact = Map.of(
                "operation", "update order status", "orderId", order.getId(),
                "before", order.getStatus().name(), "after", target.name(),
                "version", order.getVersion());
        return new PreviewData(payload, order.getVersion(), impact);
    }

    private PreviewData previewCancel(Map<String, Object> raw) {
        CommerceOrder order = activeOrder(requiredLong(raw, "orderId"));
        if (!Set.of(OrderStatus.DRAFT, OrderStatus.UNPAID).contains(order.getStatus())) {
            throw conflict("Only DRAFT or UNPAID orders may be cancelled");
        }
        return new PreviewData(
                Map.of("orderId", order.getId()), order.getVersion(),
                Map.of("operation", "cancel order", "orderId", order.getId(),
                        "before", order.getStatus().name(), "after", "CANCELLED",
                        "version", order.getVersion()));
    }

    private PreviewData previewDelete(Map<String, Object> raw, String role) {
        if (!"admin".equals(role)) throw forbidden("Soft deletion requires admin role");
        CommerceOrder order = activeOrder(requiredLong(raw, "orderId"));
        if (order.getStatus() != OrderStatus.DRAFT) throw conflict("Only DRAFT orders may be soft deleted");
        return new PreviewData(
                Map.of("orderId", order.getId()), order.getVersion(),
                Map.of("operation", "soft delete draft", "orderId", order.getId(),
                        "version", order.getVersion()));
    }

    private Map<String, Object> executeCreate(Map<String, Object> payload) {
        long customerId = requiredLong(payload, "customerId");
        Customer customer = customers.findById(customerId)
                .orElseThrow(() -> conflict("Customer was removed"));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> itemPayloads = (List<Map<String, Object>>) payload.get("items");
        List<ProductQuantity> resolved = new ArrayList<>();
        BigDecimal total = BigDecimal.ZERO;
        for (Map<String, Object> item : itemPayloads) {
            Product product = products.findById(requiredLong(item, "productId"))
                    .filter(value -> !value.isDeleted())
                    .orElseThrow(() -> conflict("Product was removed"));
            int quantity = Math.toIntExact(requiredLong(item, "quantity"));
            long expectedVersion = requiredLong(item, "productVersion");
            if (!Objects.equals(product.getVersion(), expectedVersion)) {
                throw conflict("Product changed after preview: " + product.getId());
            }
            if (product.getStock() < quantity) throw conflict("Stock changed after preview");
            resolved.add(new ProductQuantity(product, quantity));
            total = total.add(product.getPrice().multiply(BigDecimal.valueOf(quantity)));
        }
        CommerceOrder order = orders.save(CommerceOrder.draft(customer, total));
        for (ProductQuantity item : resolved) {
            orderItems.save(new OrderItem(order, item.product(), item.quantity()));
        }
        return Map.of("orderId", order.getId(), "status", "DRAFT", "totalAmount", total);
    }

    private Map<String, Object> executeStatus(PendingAction action) {
        CommerceOrder order = versionedOrder(action);
        OrderStatus target = requiredStatus(action.getNormalizedPayload(), "targetStatus");
        ensureTransition(order.getStatus(), target);
        order.changeStatus(target);
        orders.save(order);
        return Map.of("orderId", order.getId(), "status", target.name());
    }

    private Map<String, Object> executeCancel(PendingAction action) {
        CommerceOrder order = versionedOrder(action);
        if (!Set.of(OrderStatus.DRAFT, OrderStatus.UNPAID).contains(order.getStatus())) {
            throw conflict("Order can no longer be cancelled");
        }
        order.changeStatus(OrderStatus.CANCELLED);
        orders.save(order);
        return Map.of("orderId", order.getId(), "status", "CANCELLED");
    }

    private Map<String, Object> executeDelete(PendingAction action, String role) {
        if (!"admin".equals(role)) throw forbidden("Soft deletion requires admin role");
        CommerceOrder order = versionedOrder(action);
        if (order.getStatus() != OrderStatus.DRAFT) throw conflict("Order is no longer a draft");
        order.softDelete();
        orders.save(order);
        return Map.of("orderId", order.getId(), "deleted", true);
    }

    private CommerceOrder versionedOrder(PendingAction action) {
        long orderId = requiredLong(action.getNormalizedPayload(), "orderId");
        CommerceOrder order = activeOrder(orderId);
        if (!Objects.equals(order.getVersion(), action.getTargetVersion())) {
            throw conflict("Order changed after preview; create a new preview");
        }
        return order;
    }

    private CommerceOrder activeOrder(long id) {
        return orders.findByIdAndDeletedAtIsNull(id)
                .orElseThrow(() -> badRequest("Order not found: " + id));
    }

    private static void ensureTransition(OrderStatus from, OrderStatus to) {
        Map<OrderStatus, Set<OrderStatus>> allowed = Map.of(
                OrderStatus.DRAFT, Set.of(OrderStatus.UNPAID, OrderStatus.CANCELLED),
                OrderStatus.UNPAID, Set.of(OrderStatus.PAID, OrderStatus.CANCELLED),
                OrderStatus.PAID, Set.of(OrderStatus.SHIPPED),
                OrderStatus.SHIPPED, Set.of(),
                OrderStatus.CANCELLED, Set.of());
        if (!allowed.get(from).contains(to)) throw conflict("Illegal order status transition: " + from + " -> " + to);
    }

    private static long requiredLong(Map<?, ?> map, String key) {
        Object value = map.get(key);
        if (!(value instanceof Number number)) throw badRequest("Missing numeric field: " + key);
        try {
            return new BigDecimal(number.toString()).longValueExact();
        } catch (ArithmeticException | NumberFormatException ex) {
            throw badRequest("Field must be an integer: " + key);
        }
    }

    private static OrderStatus requiredStatus(Map<String, Object> map, String key) {
        Object value = map.get(key);
        try {
            return OrderStatus.valueOf(String.valueOf(value));
        } catch (IllegalArgumentException ex) {
            throw badRequest("Invalid order status");
        }
    }

    private static void requireWriter(String role) {
        if (!OPERATOR_ROLES.contains(role)) throw forbidden("Role may not modify business data");
    }

    private String canonicalJson(Map<String, Object> payload) {
        try {
            return canonicalMapper.writeValueAsString(payload);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("Cannot normalize action payload", ex);
        }
    }

    private static String randomToken() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException ex) {
            throw new IllegalStateException(ex);
        }
    }

    private static String safeMessage(RuntimeException ex) {
        return ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
    }

    private static ResponseStatusException badRequest(String reason) {
        return new ResponseStatusException(HttpStatus.BAD_REQUEST, reason);
    }

    private static ResponseStatusException forbidden(String reason) {
        return new ResponseStatusException(HttpStatus.FORBIDDEN, reason);
    }

    private static ResponseStatusException conflict(String reason) {
        return new ResponseStatusException(HttpStatus.CONFLICT, reason);
    }

    private record PreviewData(Map<String, Object> payload, Long targetVersion,
                               Map<String, Object> impact) {}
    private record ProductQuantity(Product product, int quantity) {}

    private static final class ExpiredApprovalException extends ResponseStatusException {
        private ExpiredApprovalException() {
            super(HttpStatus.CONFLICT, "Approval token has expired");
        }
    }
}
