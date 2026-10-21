<?php
/**
 * Plugin Name:       SangamPay for WooCommerce
 * Plugin URI:        https://github.com/your-org/sangampay
 * Description:       Accept UPI (incl. zero-fee direct UPI), net banking, cards, bank debits and PayPal through your self-hosted SangamPay server.
 * Version:           1.0.0
 * Requires at least: 6.0
 * Requires PHP:      7.4
 * WC requires at least: 7.0
 * License:           MIT
 * Text Domain:       sangampay
 */

if (!defined('ABSPATH')) {
    exit;
}

add_action('plugins_loaded', 'sangampay_wc_init', 11);

add_action('before_woocommerce_init', function () {
    if (class_exists(\Automattic\WooCommerce\Utilities\FeaturesUtil::class)) {
        \Automattic\WooCommerce\Utilities\FeaturesUtil::declare_compatibility('custom_order_tables', __FILE__, true);
    }
});

function sangampay_wc_init()
{
    if (!class_exists('WC_Payment_Gateway')) {
        return;
    }

    class WC_Gateway_SangamPay extends WC_Payment_Gateway
    {
        public function __construct()
        {
            $this->id                 = 'sangampay';
            $this->method_title       = 'SangamPay';
            $this->method_description = 'UPI, net banking, cards, bank debits and PayPal via your own SangamPay server. Money settles directly into your gateway or bank accounts.';
            $this->has_fields         = false;
            $this->supports           = array('products', 'refunds');

            $this->init_form_fields();
            $this->init_settings();

            $this->title          = $this->get_option('title');
            $this->description    = $this->get_option('description');
            $this->server_url     = untrailingslashit($this->get_option('server_url'));
            $this->secret_key     = $this->get_option('secret_key');
            $this->webhook_secret = $this->get_option('webhook_secret');

            add_action('woocommerce_update_options_payment_gateways_' . $this->id, array($this, 'process_admin_options'));
            add_action('woocommerce_api_sangampay', array($this, 'handle_webhook'));
        }

        public function init_form_fields()
        {
            $this->form_fields = array(
                'enabled'        => array('title' => 'Enable', 'type' => 'checkbox', 'label' => 'Enable SangamPay', 'default' => 'no'),
                'title'          => array('title' => 'Title', 'type' => 'text', 'default' => 'UPI, Cards & Net banking'),
                'description'    => array('title' => 'Description', 'type' => 'textarea',
                                          'default' => 'Pay securely with UPI, any Indian bank, cards or PayPal.'),
                'server_url'     => array('title' => 'SangamPay server URL', 'type' => 'text', 'placeholder' => 'https://pay.yourdomain.com'),
                'secret_key'     => array('title' => 'Secret key', 'type' => 'password',
                                          'description' => 'Create a key with the "developer" role in SangamPay → Developers.'),
                'webhook_secret' => array('title' => 'Webhook signing secret', 'type' => 'password',
                                          'description' => sprintf('Add a webhook endpoint in SangamPay pointing to <code>%s</code> and paste its whsec_ secret here.',
                                                                   esc_html(WC()->api_request_url('sangampay')))),
            );
        }

        private function api($method, $path, $body = null, $idempotency_key = null)
        {
            $headers = array('Authorization' => 'Bearer ' . $this->secret_key, 'Content-Type' => 'application/json');
            if ($idempotency_key) {
                $headers['Idempotency-Key'] = $idempotency_key;
            }
            $response = wp_remote_request($this->server_url . $path, array(
                'method'  => $method,
                'headers' => $headers,
                'body'    => $body === null ? null : wp_json_encode($body),
                'timeout' => 30,
            ));
            if (is_wp_error($response)) {
                throw new Exception('SangamPay is unreachable: ' . $response->get_error_message());
            }
            $data = json_decode(wp_remote_retrieve_body($response), true);
            $code = wp_remote_retrieve_response_code($response);
            if ($code >= 400) {
                throw new Exception(isset($data['error']['message']) ? $data['error']['message'] : 'SangamPay error ' . $code);
            }
            return $data;
        }

        /** Amount in the currency's smallest unit (paise for INR). */
        private function minor_units($amount, $currency)
        {
            $zero = array('JPY', 'KRW', 'VND', 'CLP', 'ISK', 'UGX', 'XAF', 'XOF');
            $three = array('BHD', 'KWD', 'OMR', 'JOD', 'TND');
            $exp = in_array($currency, $zero, true) ? 0 : (in_array($currency, $three, true) ? 3 : 2);
            return (int) round((float) $amount * pow(10, $exp));
        }

        public function process_payment($order_id)
        {
            $order = wc_get_order($order_id);
            try {
                $pi = $this->api('POST', '/v1/payment_intents', array(
                    'amount'      => $this->minor_units($order->get_total(), $order->get_currency()),
                    'currency'    => $order->get_currency(),
                    'description' => sprintf('%s order #%s', get_bloginfo('name'), $order->get_order_number()),
                    'customer'    => array_filter(array(
                        'email'   => $order->get_billing_email(),
                        'name'    => trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name()),
                        'phone'   => $order->get_billing_phone(),
                        'country' => $order->get_billing_country() ?: null,
                    )),
                    'return_url'  => $this->get_return_url($order),
                    'metadata'    => array('woocommerce_order_id' => (string) $order_id, 'order_key' => $order->get_order_key()),
                ), 'wc-order-' . $order_id . '-' . $order->get_total());
            } catch (Exception $e) {
                wc_add_notice('Payment error: ' . $e->getMessage(), 'error');
                return array('result' => 'failure');
            }
            $order->update_meta_data('_sangampay_payment_intent', $pi['id']);
            $order->update_status('pending', 'Waiting for SangamPay payment ' . $pi['id']);
            $order->save();
            return array('result' => 'success', 'redirect' => $pi['checkout_url']);
        }

        public function process_refund($order_id, $amount = null, $reason = '')
        {
            $order = wc_get_order($order_id);
            $pi = $order->get_meta('_sangampay_payment_intent');
            if (!$pi) {
                return new WP_Error('sangampay', 'No SangamPay payment on this order');
            }
            try {
                $refund = $this->api('POST', '/v1/refunds', array(
                    'payment_intent' => $pi,
                    'amount'         => $amount === null ? null : $this->minor_units($amount, $order->get_currency()),
                    'reason'         => $reason ?: null,
                ), 'wc-refund-' . $order_id . '-' . $amount . '-' . time());
            } catch (Exception $e) {
                return new WP_Error('sangampay', $e->getMessage());
            }
            $order->add_order_note(sprintf('SangamPay refund %s: %s', $refund['id'], $refund['status']));
            return true;
        }

        /** Sangam-Signature: t=<unix>,v1=<hex hmac_sha256(secret, "t.body")> */
        private function verify_signature($body, $header)
        {
            $parts = array();
            foreach (explode(',', (string) $header) as $kv) {
                $pair = explode('=', $kv, 2);
                if (count($pair) === 2) {
                    $parts[$pair[0]] = $pair[1];
                }
            }
            if (empty($parts['t']) || empty($parts['v1']) || abs(time() - (int) $parts['t']) > 300) {
                return false;
            }
            $expected = hash_hmac('sha256', $parts['t'] . '.' . $body, $this->webhook_secret);
            return hash_equals($expected, $parts['v1']);
        }

        public function handle_webhook()
        {
            $body = file_get_contents('php://input');
            $sig = isset($_SERVER['HTTP_SANGAM_SIGNATURE']) ? wp_unslash($_SERVER['HTTP_SANGAM_SIGNATURE']) : '';
            if (!$this->webhook_secret || !$this->verify_signature($body, $sig)) {
                status_header(400);
                exit('invalid signature');
            }
            $event = json_decode($body, true);
            $pi = isset($event['data']) ? $event['data'] : array();
            $order_id = isset($pi['metadata']['woocommerce_order_id']) ? (int) $pi['metadata']['woocommerce_order_id'] : 0;
            $order = $order_id ? wc_get_order($order_id) : null;
            if (!$order || $order->get_meta('_sangampay_payment_intent') !== $pi['id']) {
                status_header(200);
                exit('ignored');
            }
            switch ($event['type']) {
                case 'payment_intent.succeeded':
                    if (!$order->is_paid()) {
                        $order->payment_complete($pi['id']);
                        $order->add_order_note(sprintf('Paid via SangamPay (%s through %s).', $pi['method'], $pi['provider']));
                    }
                    break;
                case 'payment_intent.processing':
                    $order->update_status('on-hold', 'Bank debit is clearing.');
                    break;
                case 'payment_intent.requires_verification':
                    $order->update_status('on-hold', 'UPI payment submitted; waiting for UTR verification in SangamPay.');
                    break;
                case 'payment_intent.payment_failed':
                    $order->add_order_note('SangamPay attempt failed: ' . (isset($pi['failure_reason']) ? $pi['failure_reason'] : ''));
                    break;
            }
            status_header(200);
            exit('ok');
        }
    }

    add_filter('woocommerce_payment_gateways', function ($gateways) {
        $gateways[] = 'WC_Gateway_SangamPay';
        return $gateways;
    });
}
