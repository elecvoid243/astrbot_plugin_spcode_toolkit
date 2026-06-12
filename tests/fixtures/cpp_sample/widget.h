#pragma once
#include <string>

namespace ui {

class Widget {
public:
    Widget(const std::string& name);
    void render();
    int count() const;
private:
    std::string name_;
    int calls_ = 0;
};

void log_widget(const Widget& w);

}  // namespace ui
