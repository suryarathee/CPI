#include <iostream>
#include <string>
#include <ixwebsocket/IXNetSystem.h>
#include <ixwebsocket/IXWebSocket.h>
#include <cppkafka/cppkafka.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;
using namespace std;
using namespace cppkafka;

// --- CONFIGURATION ---
// We are using the live Binance Bitcoin trade stream to test the pipeline
const string BROKER_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade";
const string KAFKA_BROKERS = "localhost:9092";
const string KAFKA_TOPIC = "nse_raw_ticks";

int main() {
    // 1. Initialize Network & Kafka Producer
    ix::initNetSystem();
    
    Configuration config = {
        { "metadata.broker.list", KAFKA_BROKERS },
        { "acks", "1" } // Wait for local acknowledgement only (faster)
    };
    
    Producer kafka_producer(config);
    cout << "[SYSTEM] Kafka Producer Initialized." << endl;

    // 2. Configure WebSocket Client
    ix::WebSocket webSocket;
    webSocket.setUrl(BROKER_URL);

    // 3. The Callback (This fires every time a tick arrives)
    webSocket.setOnMessageCallback([&kafka_producer](const ix::WebSocketMessagePtr& msg) {
        
        if (msg->type == ix::WebSocketMessageType::Message) {
            try {
                // Step A: Parse the incoming data
                json raw_tick = json::parse(msg->str);
                
                // Safety check: Binance sometimes sends connection confirmation messages.
                // We only want to process actual trades (which have the "s" symbol field).
                if (raw_tick.contains("s") && raw_tick.contains("p") && raw_tick.contains("q")) {
                    
                    // Step B: Format it for our internal system
                    // Binance uses 's' (Symbol), 'p' (Price), 'q' (Quantity/Volume)
                    json formatted_tick = {
                        {"symbol", raw_tick["s"]},
                        {"price", raw_tick["p"]},
                        {"volume", raw_tick["q"]}
                    };

                    // Step C: Fire and Forget to Kafka
                    string payload = formatted_tick.dump();
                    
                    // Produce asynchronously (does not block the WebSocket thread)
                    kafka_producer.produce(MessageBuilder(KAFKA_TOPIC).payload(payload));
                }
                
            } catch (const exception& e) {
                cerr << "[ERROR] Parsing/Kafka Output Failed: " << e.what() << "\nPayload: " << msg->str << endl;
            }
        } 
        else if (msg->type == ix::WebSocketMessageType::Open) {
            cout << "[NETWORK] Connected to Live Feed (Binance BTC/USDT)!" << endl;
            cout << "[SYSTEM] Streaming data to Kafka. Check http://localhost:8080" << endl;
        }
        else if (msg->type == ix::WebSocketMessageType::Close) {
            cout << "[NETWORK] Connection Closed. Reconnecting..." << endl;
        }
        else if (msg->type == ix::WebSocketMessageType::Error) {
            cout << "[NETWORK ERROR] " << msg->errorInfo.reason << endl;
        }
    });

    // 4. Start the Engine
    cout << "[SYSTEM] Starting High-Speed Ingestion Engine..." << endl;
    webSocket.start();

    // Keep the main thread alive while the background threads do the work
    while (true) {
        // Periodically poll Kafka to handle callbacks/errors cleanly
        kafka_producer.poll(chrono::milliseconds(100)); 
    }

    // Cleanup (Unreachable in this infinite loop, but good practice)
    webSocket.stop();
    ix::uninitNetSystem();
    return 0;
}