cmake_minimum_required(VERSION 3.14)
project(NseIngester)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 1. Find the libraries
find_package(ixwebsocket REQUIRED)
find_package(CppKafka CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)

# 2. Setup Cross-platform threading
set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

# 3. Define your executable
add_executable(nse_ingester main.cpp)

# 4. Link the third-party libraries
target_link_libraries(nse_ingester PRIVATE 
    ixwebsocket::ixwebsocket 
    CppKafka::cppkafka
    nlohmann_json::nlohmann_json
    Threads::Threads
)

# 5. Link Windows Native Security & Networking Libraries (The LNK2019 Fix)
if(WIN32)
    target_link_libraries(nse_ingester PRIVATE bcrypt crypt32 ws2_32)
endif()