#include "widget.h"
#include <iostream>

namespace ui {

Widget::Widget(const std::string& name) : name_(name) {}

void Widget::render() {
    log_widget(*this);
    ++calls_;
}

int Widget::count() const {
    return calls_;
}

void log_widget(const Widget& w) {
    std::cout << w.count() << "\n";
}

}  // namespace ui
